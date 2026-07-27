import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("renders the interactive mocklist with Router and SlowTask ownership", () => {
    render(<App />);

    expect(screen.getByLabelText("输入下一条用户/工具消息")).toBeTruthy();
    expect(screen.getByRole("button", { name: "接待午饭" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "1 新任务" })).toBeTruthy();
    expect(screen.queryByText("SPAWN_SLOW_TASK")).toBeNull();
    expect(screen.getAllByText("not_run").length).toBeGreaterThan(0);
    expect(screen.getByText("No SlowTask facts yet")).toBeTruthy();
    expect(screen.getByText(/这是一个连续会话 demo/)).toBeTruthy();
    expect(screen.getByText("Request backend proposal")).toBeTruthy();
    expect(screen.getByText("No Codex result yet")).toBeTruthy();
    expect(screen.queryByText("Codex 执行过程")).toBeNull();
    expect((screen.getByLabelText("Provider") as HTMLSelectElement).value).toBe(
      "codex_cli_local",
    );
    expect((screen.getByLabelText("explicit local Codex CLI opt-in") as HTMLInputElement).checked).toBe(
      true,
    );
  });

  it("matches a material patch input, shows plan_version=2, and calls the proposal bridge", async () => {
    const { fetchMock, resolve } = stubDeferredProposalFetch();
    render(<App />);

    fireEvent.change(screen.getByLabelText("输入下一条用户/工具消息"), {
      target: { value: "改成明天上午，并且预算控制在 500 元以内。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.getAllByText("PATCH_ACTIVE_SLOW_TASK").length).toBeGreaterThan(0);
    expect(screen.getByText("plan_version=2")).toBeTruthy();
    expect(screen.queryByText("No SlowTask facts yet")).toBeNull();
    expect(screen.getAllByText("改成明天上午，并且预算控制在 500 元以内。").length).toBeGreaterThan(0);
    expect(screen.getByText("正在通过后端 bridge 调用 Codex；上方会持续显示安全的阶段和工具进度。")).toBeTruthy();

    resolve();
    await waitFor(() => expect(screen.getAllByText("PATCH_ACTIVE_SLOW_TASK").length).toBeGreaterThan(0));
    expect(screen.getAllByText("ACTIVE_TASK_PATCH").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/current_plan_version/i).length).toBeGreaterThan(0);
    expect(screen.getByText("plan_version=2")).toBeTruthy();
    expect(screen.getAllByText(/USER_PATCH_RECEIVED/).length).toBeGreaterThan(0);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/workbench/codex-proposal");
    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const payload = JSON.parse(String(requestInit.body)) as {
      provider_mode: string;
      allow_local_codex_cli: boolean;
    };
    expect(payload.provider_mode).toBe("codex_cli_local");
    expect(payload.allow_local_codex_cli).toBe(true);
    await waitFor(() => expect(screen.getAllByText("python_codex_cli_local").length).toBeGreaterThan(0));
  });

  it("only shows stale evidence and confirmation-gate facts after running the selected scenario", async () => {
    const fetchMock = stubProposalFetch();
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "3 旧结果" }));
    expect(screen.getByText("No SlowTask facts yet")).toBeTruthy();
    expect(screen.queryByText(/Stale evidence bucket/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText(/Stale evidence bucket/)).toBeTruthy());
    expect(screen.getByText(/TOOL_RESULT_MARKED_STALE/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "4 确认" }));
    expect(screen.getByText(/Stale evidence bucket/)).toBeTruthy();
    expect((screen.getByLabelText("输入下一条用户/工具消息") as HTMLTextAreaElement).value).toContain(
      "取消",
    );

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByText("CANCEL_OR_PAUSE_CANDIDATE").length).toBeGreaterThan(0));
    expect(screen.getByText(/Pending confirmation/)).toBeTruthy();
    expect(screen.getAllByText(/WAITING_FOR_USER_CONFIRMATION/).length).toBeGreaterThan(0);
  });

  it("builds a dynamic runtime snapshot for non-mocklist input before requesting Codex analysis", async () => {
    const fetchMock = stubProposalFetch();
    render(<App />);

    const customInput = "请评估一个没有写进清单的准备事项。";
    fireEvent.change(screen.getByLabelText("输入下一条用户/工具消息"), {
      target: { value: customInput },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getAllByText("dynamic_router").length).toBeGreaterThan(0));

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const payload = JSON.parse(String(requestInit.body)) as {
      intent: string;
      snapshot: { task: { evidence: Array<{ summary: string }> } };
    };
    expect(payload.intent).toBe(customInput);
    expect(payload.snapshot.task.evidence[0].summary).toContain(customInput);
  });

  it("keeps the Yunnan reception planning flow in one transcript", async () => {
    const fetchMock = stubProposalFetch();
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "接待午饭" }));
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getAllByText("请帮忙规划一个接待午饭，选云南菜。").length).toBeGreaterThan(0),
    );
    expect(screen.getByText("缺失字段审查")).toBeTruthy();
    expect(screen.getByText(/memory:\/\/session\/reception_meal\/preferences\/v1/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "改晚上" }));
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getAllByText("请帮忙规划一个接待午饭，选云南菜。").length).toBeGreaterThan(0);
    expect(screen.getAllByText("中间我插一句，把时间修改到晚上吧。").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/TOOL_RESULT_MARKED_STALE/).length).toBeGreaterThan(0);
  });

  it("shows Week 2 evidence trust labels without mixing hypotheses into authoritative evidence", async () => {
    const fetchMock = stubProposalFetch();
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "补接待数据" }));
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Authoritative evidence")).toBeTruthy();
    expect(screen.getByText("Untrusted web evidence")).toBeTruthy();
    expect(screen.getAllByText(/trust=authoritative/).length).toBeGreaterThan(0);
    expect(screen.getByText(/trust=untrusted_web_evidence/)).toBeTruthy();
    expect(screen.getByText(/UNTRUSTED_WEB_EVIDENCE/)).toBeTruthy();
  });
});

function buildProposalResponse() {
  return {
    ok: true,
    json: async () => ({
      backend: "python_slow_system_workbench_codex",
      capability: {
        output_mode: "real",
        provider: "codex_cli",
        health_status: "available",
      },
      proposal: {
        proposal_id: "proposal_test_validated",
        proposal_type: "plan_update",
        status: "validated",
        summary: "Codex bridge test proposal.",
        suggested_next_steps: ["Review runtime input evidence."],
        missing_fields: [],
        requires_confirmation: false,
        risk_notes: ["Proposal only."],
        source_evidence_refs: ["evidence://runtime/test/user_input"],
        safety: {
          codex_is_fact_owner: false,
          advances_plan_version: false,
          authorizes_tool: false,
          contains_secret: false,
          mutates_task_snapshot: false,
          emits_canonical_event: false,
          executes_external_tool: false,
          contains_raw_provider_body: false,
        },
      },
    }),
  };
}

function stubProposalFetch() {
  const fetchMock = vi.fn(async (_input: string, _init?: RequestInit) => buildProposalResponse());
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stubDeferredProposalFetch() {
  let resolveResponse!: () => void;
  const responseGate = new Promise<void>((resolve) => {
    resolveResponse = resolve;
  });
  const fetchMock = vi.fn(async (_input: string, _init?: RequestInit) => {
    await responseGate;
    return buildProposalResponse();
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, resolve: resolveResponse };
}
