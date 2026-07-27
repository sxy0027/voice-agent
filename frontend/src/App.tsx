import { useEffect, useMemo, useRef, useState } from "react";

import {
  type CodexProviderMode,
  confirmWorkbenchSession,
  requestCodexProposal,
  workbenchSnapshotToScenario,
  type CodexProposalResult,
} from "./adapters/codexProposalClient";
import { CodexProposalPanel } from "./components/CodexProposalPanel";
import { ConversationPanel } from "./components/ConversationPanel";
import { PlanEvidencePanel } from "./components/PlanEvidencePanel";
import { SlowTaskTimeline } from "./components/SlowTaskTimeline";
import { StatusSidebar } from "./components/StatusSidebar";
import {
  buildRuntimeScenarioForInput,
  slowSystemScenarios,
} from "./mockScenarios";
import type {
  CodexProposal,
  ConversationTurn,
  InputMatchMode,
  SlowSystemScenario,
} from "./slowSystem";

const initialTranscriptTurns: readonly ConversationTurn[] = [
  {
    id: "system_welcome",
    speaker: "system",
    text:
      "这是一个连续会话 demo。侧边示例只会把消息放进输入框；每次提交都会追加到聊天记录，并通过 Router / SlowTask / Codex proposal bridge 展示过程。",
    owner: "react_ui",
    note: "UI owns display state only; SlowTask facts remain owned by runtime snapshots and event journal.",
  },
];

function userTranscriptTurn(
  text: string,
  scenario: SlowSystemScenario,
  sequence: number,
): ConversationTurn {
  return {
    id: `turn_${sequence}_user`,
    speaker: scenario.demoAction === "receive_late_tool_result" ? "tool" : "user",
    text,
    owner: scenario.demoAction === "receive_late_tool_result" ? "tool_executor" : "event_journal",
    note:
      scenario.demoAction === "receive_late_tool_result"
        ? "工具结果以原始 plan_version 进入；SlowTask 决定是否 stale/adopt。"
        : "用户输入先进入 turn ingress / Event Journal，再由 Router 和 SlowTask 处理。",
  };
}

function assistantTranscriptTurn(
  scenario: SlowSystemScenario,
  proposal: CodexProposal,
  sequence: number,
  runtimeAnswer?: string,
): ConversationTurn {
  const pending = scenario.slowTask.pendingConfirmation;
  const missingFields = scenario.slowTask.lifecycleState === "WAITING_FOR_SLOT"
    ? scenario.slowTask.missingFields.length > 0
      ? scenario.slowTask.missingFields
      : proposal.missingFields
    : [];
  const text = runtimeAnswer || (pending
    ? `${pending.prompt}\n\n${pending.riskSummary}`
    : missingFields.length > 0
      ? `我已经记录了需求，但还需要你补充 ${userFacingMissingFields(missingFields)}。请直接给出可定位的信息，例如“北京市海淀区中关村领展购物广场附近”，以及“7 月 25 日 18:30 的晚餐”。在这些信息确定前，我不会把猜测当成餐厅候选。`
      : scenario.slowTask.lifecycleState === "EXECUTING"
        ? "好的，信息够了。我先按你给的范围去查一个沙盒里的候选方案；你也可以继续补充预算、人数或偏好。"
        : scenario.slowTask.lifecycleState === "CANCELLED"
          ? "好的，这个任务我已经按你的确认取消了，不会继续推进。"
          : scenario.slowTask.lifecycleState === "COMPLETED"
            ? scenario.answer
            : proposal.summary);
  return {
    id: `turn_${sequence}_assistant`,
    speaker: "assistant_fast",
    text,
    owner: scenario.slowTask.lifecycleState === "COMPLETED" ? "composer" : "codex_proposal",
    note:
      scenario.slowTask.lifecycleState === "COMPLETED"
        ? "最终表达必须覆盖 SlowTask SemanticCommitment；Composer 只负责表达，不改写事实。"
        : "面向用户的回复只表达当前可承诺状态；调试字段留在 snapshot/timeline/proposal 面板。",
  };
}

function userFacingMissingFields(fields: readonly string[]): string {
  const labels: Record<string, string> = {
    time_window: "具体时间",
    location_anchor: "地点范围",
    budget_or_time_preference: "预算或时间偏好",
    party_size: "人数",
  };
  return fields.map((field) => labels[field] ?? field).join("、");
}

function errorTranscriptTurn(message: string, sequence: number): ConversationTurn {
  return {
    id: `turn_${sequence}_error`,
    speaker: "system",
    text: message,
    owner: "react_ui",
    note: "Codex bridge failed closed; no static hidden answer is substituted.",
  };
}

function App() {
  const [selectedScenarioId, setSelectedScenarioId] = useState<string | null>(null);
  const selectedScenario = useMemo(
    () =>
      slowSystemScenarios.find((scenario) => scenario.id === selectedScenarioId) ??
      slowSystemScenarios[0],
    [selectedScenarioId],
  );

  const [inputText, setInputText] = useState("");
  const [runScenario, setRunScenario] = useState<SlowSystemScenario>(selectedScenario);
  const [hasRun, setHasRun] = useState(false);
  const [matchedBy, setMatchedBy] = useState<InputMatchMode>("scenario_button");
  const [selectedTimelineEventId, setSelectedTimelineEventId] = useState(
    selectedScenario.timeline[0].id,
  );
  const [proposal, setProposal] = useState<CodexProposal | null>(null);
  const [providerMode, setProviderMode] = useState<CodexProviderMode>("codex_cli_local");
  const [allowLocalCodexCli, setAllowLocalCodexCli] = useState(true);
  const [proposalLoading, setProposalLoading] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [transcriptTurns, setTranscriptTurns] = useState<readonly ConversationTurn[]>(
    initialTranscriptTurns,
  );
  const transcriptSeqRef = useRef(initialTranscriptTurns.length);
  const requestSeqRef = useRef(0);
  const streamCleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      streamCleanupRef.current?.();
      streamCleanupRef.current = null;
    };
  }, []);

  const selectedTimelineEvent =
    runScenario.timeline.find((event) => event.id === selectedTimelineEventId) ??
    runScenario.timeline[0];

  const runMockInput = async () => {
    const trimmedInput = inputText.trim();
    if (!trimmedInput) {
      setProposalError("请输入 mocklist 消息或一个需要慢系统分析的问题。");
      return;
    }

    const result = buildRuntimeScenarioForInput(trimmedInput, selectedScenario);
    const nextScenario = result.scenario;
    setSelectedScenarioId(result.selectedScenarioId);
    setRunScenario(nextScenario);
    setMatchedBy(result.matchedBy);
    setSelectedTimelineEventId(nextScenario.timeline[0]?.id ?? "");
    setHasRun(true);
    setProposal(null);
    setProposalError(null);
    transcriptSeqRef.current += 1;
    const userSequence = transcriptSeqRef.current;
    setTranscriptTurns((turns) => [
      ...turns,
      userTranscriptTurn(trimmedInput, nextScenario, userSequence),
    ]);
    const requestSeq = requestSeqRef.current + 1;
    requestSeqRef.current = requestSeq;
    const backendProposal = await requestProposalForScenario(nextScenario, trimmedInput, requestSeq);
    if (!backendProposal || requestSeqRef.current !== requestSeq) {
      return;
    }
    const effectiveScenario = backendProposal.runtimeSnapshot
      ? workbenchSnapshotToScenario(
          backendProposal.runtimeSnapshot,
          trimmedInput,
          nextScenario,
          backendProposal,
        )
      : nextScenario;
    if (!backendProposal.runtimeSnapshot) {
      setRunScenario(nextScenario);
      setMatchedBy(result.matchedBy);
      setSelectedTimelineEventId(nextScenario.timeline[0].id);
    }
    transcriptSeqRef.current += 1;
    const assistantSequence = transcriptSeqRef.current;
    setTranscriptTurns((turns) => [
      ...turns,
      assistantTranscriptTurn(
        effectiveScenario,
        backendProposal,
        assistantSequence,
        latestRuntimeAssistantAnswer(effectiveScenario),
      ),
    ]);
    setHasRun(true);
  };

  const selectScenario = (scenarioId: string) => {
    const nextScenario =
      slowSystemScenarios.find((scenario) => scenario.id === scenarioId) ?? slowSystemScenarios[0];
    setSelectedScenarioId(scenarioId);
    setInputText(nextScenario.mockInput);
    setProposalError(null);
    if (!hasRun) {
      setRunScenario(nextScenario);
      setMatchedBy("scenario_button");
      setSelectedTimelineEventId(nextScenario.timeline[0].id);
    }
  };

  const updateInputText = (value: string) => {
    setInputText(value);
    setProposalError(null);
  };

  const updateProviderMode = (mode: CodexProviderMode) => {
    requestSeqRef.current += 1;
    streamCleanupRef.current?.();
    streamCleanupRef.current = null;
    setProviderMode(mode);
    setAllowLocalCodexCli(mode === "codex_cli_local");
    setSessionId(null);
    setProposalLoading(false);
    setProposal(null);
    setProposalError(null);
  };

  const requestProposalForScenario = async (
    scenario: SlowSystemScenario,
    intent: string,
    requestSeq = requestSeqRef.current + 1,
  ): Promise<CodexProposalResult | null> => {
    requestSeqRef.current = requestSeq;
    setProposalLoading(true);
    setProposalError(null);
    setProposal(null);
    try {
      const backendProposal = await requestCodexProposal({
        scenario,
        intent,
        providerMode,
        allowLocalCodexCli,
        sessionId,
        onSessionCreated: (createdSessionId, snapshot) => {
          if (requestSeqRef.current !== requestSeq) {
            return;
          }
          setSessionId(createdSessionId);
          const dynamicScenario = workbenchSnapshotToScenario(
            snapshot,
            intent,
            scenario,
            scenario.codexProposal,
          );
          setRunScenario(dynamicScenario);
          setSelectedScenarioId(null);
          setMatchedBy("dynamic_router");
          setSelectedTimelineEventId(dynamicScenario.timeline[0]?.id ?? "");
          setHasRun(true);
        },
        onSnapshot: (snapshot) => {
          if (requestSeqRef.current !== requestSeq) {
            return;
          }
          const dynamicScenario = workbenchSnapshotToScenario(
            snapshot,
            intent,
            scenario,
            proposal ?? scenario.codexProposal,
          );
          setRunScenario(dynamicScenario);
          setSelectedScenarioId(null);
          setMatchedBy("dynamic_router");
          setSelectedTimelineEventId(dynamicScenario.timeline[0]?.id ?? "");
          setHasRun(true);
        },
        onStreamStarted: (stop) => {
          streamCleanupRef.current?.();
          streamCleanupRef.current = stop;
        },
      });
      if (requestSeqRef.current !== requestSeq) {
        return null;
      }
      if (backendProposal.sessionId && backendProposal.runtimeSnapshot) {
        setSessionId(backendProposal.sessionId);
        const dynamicScenario = workbenchSnapshotToScenario(
          backendProposal.runtimeSnapshot,
          intent,
          scenario,
          backendProposal,
        );
        setRunScenario(dynamicScenario);
        setSelectedScenarioId(null);
        setMatchedBy("dynamic_router");
        setSelectedTimelineEventId(dynamicScenario.timeline[0]?.id ?? "");
      }
      setProposal(backendProposal);
      return backendProposal;
    } catch (error) {
      if (requestSeqRef.current === requestSeq) {
        const message = error instanceof Error ? error.message : "Codex proposal request failed";
        setProposalError(message);
        transcriptSeqRef.current += 1;
        const errorSequence = transcriptSeqRef.current;
        setTranscriptTurns((turns) => [...turns, errorTranscriptTurn(message, errorSequence)]);
      }
      return null;
    } finally {
      if (requestSeqRef.current === requestSeq) {
        setProposalLoading(false);
      }
    }
  };

  const requestProposal = async () => {
    const trimmedInput = inputText.trim();
    if (!trimmedInput) {
      setProposalError("请输入 mocklist 消息或一个需要慢系统分析的问题。");
      return;
    }
    if (!hasRun) {
      await runMockInput();
      return;
    }
    const requestSeq = requestSeqRef.current + 1;
    requestSeqRef.current = requestSeq;
    await requestProposalForScenario(runScenario, trimmedInput, requestSeq);
  };

  const confirmCurrentTask = async (accepted: boolean) => {
    const pending = runScenario.slowTask.pendingConfirmation;
    if (!sessionId || !pending) {
      setProposalError("当前页面没有 Python-owned pending confirmation。");
      return;
    }
    const requestSeq = requestSeqRef.current + 1;
    requestSeqRef.current = requestSeq;
    setProposalLoading(true);
    setProposalError(null);
    try {
      const snapshot = await confirmWorkbenchSession(sessionId, pending.confirmationId, accepted);
      if (requestSeqRef.current !== requestSeq) {
        return;
      }
      const dynamicScenario = workbenchSnapshotToScenario(
        snapshot,
        accepted ? "确认取消当前任务" : "保留当前任务，不取消",
        runScenario,
        proposal ?? runScenario.codexProposal,
      );
      setRunScenario(dynamicScenario);
      setSelectedTimelineEventId(dynamicScenario.timeline[0]?.id ?? "");
      setHasRun(true);
      transcriptSeqRef.current += 1;
      const userSequence = transcriptSeqRef.current;
      transcriptSeqRef.current += 1;
      const assistantSequence = transcriptSeqRef.current;
      const confirmationText = accepted ? "确认取消当前任务。" : "保留当前任务，不取消。";
      setTranscriptTurns((turns) => [
        ...turns,
        userTranscriptTurn(confirmationText, dynamicScenario, userSequence),
        {
          id: `turn_${assistantSequence}_confirmation`,
          speaker: "assistant_fast",
          text: accepted
            ? "已记录用户确认，SlowTask 会按 current-plan confirmation 终止任务。"
            : "已记录用户拒绝取消，SlowTask 继续保留当前任务。",
          owner: "composer",
          note: "confirmation 结果来自 Python-owned SlowTask snapshot，不是 React 直接改事实。",
        },
      ]);
    } catch (error) {
      setProposalError(error instanceof Error ? error.message : "Workbench confirmation failed");
    } finally {
      if (requestSeqRef.current === requestSeq) {
        setProposalLoading(false);
      }
    }
  };

  const markProposal = (status: "accepted" | "rejected") => {
    if (!proposal) {
      return;
    }
    setProposal({
      ...proposal,
      status,
      boundaryWarning:
        status === "accepted"
          ? "Demo UI 标记为 accepted 只表示 human 接受 proposal 供后续工作参考；它仍未直接修改 SlowTask facts。"
          : "Demo UI 标记为 rejected；SlowTask facts 保持不变。",
    });
  };

  return (
    <main className="app-shell">
      <StatusSidebar
        scenarios={slowSystemScenarios}
        selectedScenarioId={selectedScenarioId}
        runScenario={runScenario}
        hasRun={hasRun}
        loading={proposalLoading}
        matchedBy={matchedBy}
        proposal={proposal}
        onSelectScenario={selectScenario}
      />

      <section className="workbench-main" aria-label="Slow system conversation workbench">
        <header className="app-header">
          <div>
            <p className="eyebrow">voice-agent / Slow System Demo Workbench</p>
            <h1>Slow System Workbench</h1>
          </div>
          <div className="header-status" aria-label="Current routing summary">
            <span>{hasRun ? runScenario.routerDecision : "not_run"}</span>
            <span>{hasRun ? runScenario.taskFocus : "waiting_for_input"}</span>
          </div>
        </header>

        <ConversationPanel
          runScenario={runScenario}
          hasRun={hasRun}
          inputText={inputText}
          matchedBy={matchedBy}
          proposal={proposal}
          proposalError={proposalError}
          loading={proposalLoading}
          transcriptTurns={transcriptTurns}
          onInputTextChange={updateInputText}
          onRunMockInput={runMockInput}
        />

        {hasRun ? (
          <SlowTaskTimeline
            scenario={runScenario}
            selectedEvent={selectedTimelineEvent}
            onSelectEvent={setSelectedTimelineEventId}
          />
        ) : (
          <section className="panel timeline-panel" aria-labelledby="timeline-heading">
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">SlowTask timeline</p>
                <h2 id="timeline-heading">Mock Event Flow</h2>
              </div>
              <span className="status-pill">not_run</span>
            </div>
            <div className="proposal-empty">
              <strong>No event flow yet</strong>
              <p>选择场景只会填充输入框；点击运行后才会生成 Router / SlowTask timeline。</p>
            </div>
          </section>
        )}
      </section>

      <aside className="context-rail" aria-label="SlowTask context and Codex proposal">
        <PlanEvidencePanel
          scenario={runScenario}
          selectedEvent={selectedTimelineEvent}
          hasRun={hasRun}
          loading={proposalLoading}
          proposal={proposal}
          sessionId={sessionId}
          onConfirm={confirmCurrentTask}
        />

        <CodexProposalPanel
          proposal={proposal}
          providerMode={providerMode}
          allowLocalCodexCli={allowLocalCodexCli}
          loading={proposalLoading}
          error={proposalError}
          onProviderModeChange={updateProviderMode}
          onAllowLocalCodexCliChange={setAllowLocalCodexCli}
          onRequestProposal={requestProposal}
          onMarkProposal={markProposal}
        />
      </aside>
    </main>
  );
}

function latestRuntimeAssistantAnswer(scenario: SlowSystemScenario): string | undefined {
  const latest = [...scenario.conversation]
    .reverse()
    .find((turn) => turn.speaker === "assistant_fast" || turn.speaker === "system");
  return latest?.text || undefined;
}

export default App;
