import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";

import type { Plugin } from "vite";

type JsonObject = Record<string, unknown>;

type PendingRequest = Readonly<{
  resolve: (value: JsonObject) => void;
  reject: (reason?: unknown) => void;
}>;

class PythonWorkbenchWorker {
  private child: ChildProcessWithoutNullStreams | null = null;
  private buffer = "";
  private nextRequestId = 1;
  private readonly pending = new Map<number, PendingRequest>();

  public constructor(private readonly repoRoot: string) {}

  public call(operation: string, payload: JsonObject = {}): Promise<JsonObject> {
    this.ensureStarted();
    const child = this.child;
    if (!child) {
      return Promise.reject(new Error("Python Workbench worker is not available"));
    }
    const requestId = this.nextRequestId++;
    return new Promise<JsonObject>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
      const request = JSON.stringify({ request_id: requestId, operation, payload });
      child.stdin.write(`${request}\n`, (error) => {
        if (!error) {
          return;
        }
        this.pending.delete(requestId);
        reject(error);
      });
    });
  }

  public close(): void {
    if (this.child) {
      this.child.kill();
      this.child = null;
    }
    this.rejectPending(new Error("Python Workbench worker closed"));
  }

  private ensureStarted(): void {
    if (this.child) {
      return;
    }
    const python = process.env.VOICE_AGENT_PYTHON || "python3";
    const existingPythonPath = process.env.PYTHONPATH;
    const pythonPath = existingPythonPath
      ? `${path.join(this.repoRoot, "src")}${path.delimiter}${existingPythonPath}`
      : path.join(this.repoRoot, "src");
    const child = spawn(python, ["-m", "voice_agent.runtime.slow_system_workbench_api"], {
      cwd: this.repoRoot,
      env: {
        ...process.env,
        PYTHONPATH: pythonPath,
        PYTHONDONTWRITEBYTECODE: "1",
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child = child;
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => this.consumeStdout(chunk));
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", () => {
      // Provider/process diagnostics stay on the server side.  They are not
      // forwarded to the browser or written into the event journal.
    });
    child.on("error", (error) => this.failWorker(error));
    child.on("close", (code) => {
      this.child = null;
      if (code !== 0) {
        this.failWorker(new Error(`Python Workbench worker exited with code ${code ?? "unknown"}`));
      }
    });
  }

  private consumeStdout(chunk: string): void {
    this.buffer += chunk;
    let newlineIndex = this.buffer.indexOf("\n");
    while (newlineIndex >= 0) {
      const line = this.buffer.slice(0, newlineIndex).trim();
      this.buffer = this.buffer.slice(newlineIndex + 1);
      if (line) {
        try {
          const response = JSON.parse(line) as JsonObject;
          const requestId = Number(response.request_id);
          const pending = this.pending.get(requestId);
          if (pending) {
            this.pending.delete(requestId);
            pending.resolve(response);
          }
        } catch {
          this.failWorker(new Error("Python Workbench worker returned invalid JSON"));
          return;
        }
      }
      newlineIndex = this.buffer.indexOf("\n");
    }
  }

  private failWorker(error: Error): void {
    this.rejectPending(new Error(safeWorkerError(error)));
    if (this.child) {
      this.child.kill();
      this.child = null;
    }
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      pending.reject(error);
    }
    this.pending.clear();
  }
}

export function workbenchCodexPlugin(): Plugin {
  return {
    name: "voice-agent-workbench-codex",
    configureServer(server) {
      const repoRoot = path.resolve(server.config.root, "..");
      const worker = new PythonWorkbenchWorker(repoRoot);
      server.httpServer?.once("close", () => worker.close());

      server.middlewares.use(async (req, res, next) => {
        const pathname = new URL(req.url ?? "/", "http://localhost").pathname;
        if (!pathname.startsWith("/api/workbench")) {
          next();
          return;
        }

        try {
          if (pathname === "/api/workbench/codex-proposal") {
            if (req.method !== "POST") {
              next();
              return;
            }
            const body = await readJsonBody(req);
            const result = await worker.call("compat_proposal", body);
            sendJson(res, result.ok === false ? 400 : 200, result);
            return;
          }

          const route = parseWorkbenchRoute(pathname, req.method ?? "GET");
          if (!route) {
            next();
            return;
          }
          if (route.kind === "stream") {
            await serveWorkbenchStream(req, res, worker, route.sessionId);
            return;
          }
          const body = req.method === "GET" ? {} : await readJsonBody(req);
          const result = await worker.call(route.operation, {
            ...body,
            ...(route.sessionId ? { session_id: route.sessionId } : {}),
            ...(route.confirmationId ? { confirmation_id: route.confirmationId } : {}),
          });
          sendJson(res, result.ok === false ? 400 : 200, result);
        } catch (error) {
          sendJson(res, 500, {
            ok: false,
            error: "workbench_transport_failed",
            message: safeWorkerError(error),
          });
        }
      });
    },
  };
}

type WorkbenchRoute = Readonly<{
  kind: "json" | "stream";
  operation: "create_session" | "snapshot" | "message" | "confirmation" | "reset";
  sessionId?: string;
  confirmationId?: string;
}>;

function parseWorkbenchRoute(pathname: string, method: string): WorkbenchRoute | null {
  if (pathname === "/api/workbench/sessions" && method === "POST") {
    return { kind: "json", operation: "create_session" };
  }
  const snapshotMatch = pathname.match(/^\/api\/workbench\/sessions\/([^/]+)\/snapshot$/);
  if (snapshotMatch && method === "GET") {
    return { kind: "json", operation: "snapshot", sessionId: decodeURIComponent(snapshotMatch[1]) };
  }
  const messageMatch = pathname.match(/^\/api\/workbench\/sessions\/([^/]+)\/messages$/);
  if (messageMatch && method === "POST") {
    return { kind: "json", operation: "message", sessionId: decodeURIComponent(messageMatch[1]) };
  }
  const confirmationMatch = pathname.match(
    /^\/api\/workbench\/sessions\/([^/]+)\/confirmations\/([^/]+)$/,
  );
  if (confirmationMatch && method === "POST") {
    return {
      kind: "json",
      operation: "confirmation",
      sessionId: decodeURIComponent(confirmationMatch[1]),
      confirmationId: decodeURIComponent(confirmationMatch[2]),
    };
  }
  const resetMatch = pathname.match(/^\/api\/workbench\/sessions\/([^/]+)\/reset$/);
  if (resetMatch && method === "POST") {
    return { kind: "json", operation: "reset", sessionId: decodeURIComponent(resetMatch[1]) };
  }
  const streamMatch = pathname.match(/^\/api\/workbench\/sessions\/([^/]+)\/stream$/);
  if (streamMatch && method === "GET") {
    return { kind: "stream", operation: "snapshot", sessionId: decodeURIComponent(streamMatch[1]) };
  }
  return null;
}

async function serveWorkbenchStream(
  req: IncomingMessage,
  res: ServerResponse,
  worker: PythonWorkbenchWorker,
  sessionId: string | undefined,
): Promise<void> {
  if (!sessionId) {
    sendJson(res, 400, { ok: false, error: "session_id_required" });
    return;
  }
  res.statusCode = 200;
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders?.();
  let closed = false;
  let lastSnapshotId = "";
  let pollTimer: ReturnType<typeof setInterval> | undefined;
  const stop = () => {
    closed = true;
    if (pollTimer) {
      clearInterval(pollTimer);
    }
  };
  req.on("close", stop);

  const publish = async () => {
    if (closed) {
      return;
    }
    try {
      const result = await worker.call("snapshot", { session_id: sessionId });
      const snapshot = result.snapshot as JsonObject | undefined;
      const snapshotId = typeof snapshot?.snapshot_id === "string" ? snapshot.snapshot_id : "";
      if (!snapshot || snapshotId === lastSnapshotId || closed) {
        return;
      }
      lastSnapshotId = snapshotId;
      res.write(`event: snapshot\ndata: ${JSON.stringify(snapshot)}\n\n`);
    } catch {
      if (!closed) {
        res.write(`event: error\ndata: ${JSON.stringify({ error: "workbench_stream_failed" })}\n\n`);
      }
    }
  };

  await publish();
  pollTimer = setInterval(() => {
    void publish();
  }, 250);
  req.on("close", () => clearInterval(pollTimer));
}

async function readJsonBody(req: IncomingMessage): Promise<JsonObject> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const raw = Buffer.concat(chunks).toString("utf8");
  if (!raw.trim()) {
    return {};
  }
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("request body must be a JSON object");
  }
  return parsed as JsonObject;
}

function safeWorkerError(error: unknown): string {
  const message = error instanceof Error ? error.message : "Unknown Workbench worker error";
  return message
    .replace(/Bearer\s+\S+/gi, "[redacted]")
    .replace(new RegExp("/" + "Users" + "/[^\\s\"']+", "g"), "[redacted-local-path]")
    .slice(0, 400);
}

function sendJson(res: ServerResponse, statusCode: number, payload: unknown): void {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}
