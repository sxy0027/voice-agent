import { spawn } from "node:child_process";
import path from "node:path";
import type { IncomingMessage, ServerResponse } from "node:http";

import type { Plugin } from "vite";

const PYTHON_BRIDGE_SCRIPT = `
import json
import sys

from voice_agent.runtime.slow_system_workbench_codex import (
    build_workbench_codex_proposal_capability,
    request_workbench_codex_proposal,
)

payload = json.load(sys.stdin)
provider_mode = payload.get("provider_mode", "fake")
allow_local_codex_cli = bool(payload.get("allow_local_codex_cli", False))
proposal = request_workbench_codex_proposal(
    snapshot=payload["snapshot"],
    intent=payload["intent"],
    proposal_type=payload["proposal_type"],
    source_evidence_refs=payload["source_evidence_refs"],
    provider_mode=provider_mode,
    allow_local_codex_cli=allow_local_codex_cli,
    codex_cli_timeout_seconds=int(payload.get("timeout_seconds", 45)),
)
capability = build_workbench_codex_proposal_capability(
    provider_mode=provider_mode,
    allow_local_codex_cli=allow_local_codex_cli,
)
print(json.dumps({
    "backend": "python_slow_system_workbench_codex",
    "proposal": proposal,
    "capability": capability,
}, ensure_ascii=False, sort_keys=True))
`;

export function workbenchCodexPlugin(): Plugin {
  return {
    name: "voice-agent-workbench-codex",
    configureServer(server) {
      const repoRoot = path.resolve(server.config.root, "..");
      server.middlewares.use("/api/workbench/codex-proposal", async (req, res, next) => {
        if (req.method !== "POST") {
          next();
          return;
        }

        try {
          const body = await readBody(req);
          const result = await runPythonBridge(repoRoot, body);
          sendJson(res, 200, result);
        } catch (error) {
          sendJson(res, 500, {
            error: "codex_proposal_bridge_failed",
            message: error instanceof Error ? error.message : "Unknown bridge error",
          });
        }
      });
    },
  };
}

async function readBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function runPythonBridge(repoRoot: string, body: string): Promise<unknown> {
  const python = process.env.VOICE_AGENT_PYTHON || "python3";
  const existingPythonPath = process.env.PYTHONPATH;
  const pythonPath = existingPythonPath
    ? `${path.join(repoRoot, "src")}${path.delimiter}${existingPythonPath}`
    : path.join(repoRoot, "src");

  return new Promise((resolve, reject) => {
    const child = spawn(python, ["-c", PYTHON_BRIDGE_SCRIPT], {
      cwd: repoRoot,
      env: {
        ...process.env,
        PYTHONPATH: pythonPath,
        PYTHONDONTWRITEBYTECODE: "1",
      },
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (data) => {
      stdout += data;
    });
    child.stderr.on("data", (data) => {
      stderr += data;
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(safeBridgeError(stderr)));
        return;
      }

      try {
        resolve(JSON.parse(stdout));
      } catch (error) {
        reject(error);
      }
    });
    child.stdin.end(body);
  });
}

function safeBridgeError(stderr: string): string {
  if (!stderr.trim()) {
    return "Python bridge exited without details";
  }
  const localUserPathPattern = new RegExp("/" + "Users" + "/[^\\s\"']+", "g");
  return stderr
    .replace(/Bearer\s+\S+/gi, "[redacted]")
    .replace(localUserPathPattern, "[redacted-local-path]")
    .slice(0, 800);
}

function sendJson(res: ServerResponse, statusCode: number, payload: unknown) {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}
