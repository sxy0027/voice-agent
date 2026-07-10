import { useMemo, useRef, useState } from "react";

import {
  type CodexProviderMode,
  requestCodexProposal,
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
import type { CodexProposal, InputMatchMode, SlowSystemScenario } from "./slowSystem";

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
  const requestSeqRef = useRef(0);

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
    setHasRun(false);
    setProposal(null);
    setProposalError(null);
    const requestSeq = requestSeqRef.current + 1;
    requestSeqRef.current = requestSeq;
    const backendProposal = await requestProposalForScenario(nextScenario, trimmedInput, requestSeq);
    if (!backendProposal || requestSeqRef.current !== requestSeq) {
      return;
    }
    setRunScenario(nextScenario);
    setMatchedBy(result.matchedBy);
    setSelectedTimelineEventId(nextScenario.timeline[0].id);
    setHasRun(true);
  };

  const selectScenario = (scenarioId: string) => {
    requestSeqRef.current += 1;
    const nextScenario =
      slowSystemScenarios.find((scenario) => scenario.id === scenarioId) ?? slowSystemScenarios[0];
    setSelectedScenarioId(scenarioId);
    setInputText(nextScenario.mockInput);
    setRunScenario(nextScenario);
    setHasRun(false);
    setMatchedBy("scenario_button");
    setSelectedTimelineEventId(nextScenario.timeline[0].id);
    setProposalLoading(false);
    setProposal(null);
    setProposalError(null);
  };

  const updateInputText = (value: string) => {
    requestSeqRef.current += 1;
    setInputText(value);
    setHasRun(false);
    setProposalLoading(false);
    setProposal(null);
    setProposalError(null);
  };

  const updateProviderMode = (mode: CodexProviderMode) => {
    requestSeqRef.current += 1;
    setProviderMode(mode);
    setAllowLocalCodexCli(mode === "codex_cli_local");
    setHasRun(false);
    setProposalLoading(false);
    setProposal(null);
    setProposalError(null);
  };

  const requestProposalForScenario = async (
    scenario: SlowSystemScenario,
    intent: string,
    requestSeq = requestSeqRef.current + 1,
  ): Promise<CodexProposal | null> => {
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
      });
      if (requestSeqRef.current !== requestSeq) {
        return null;
      }
      setProposal(backendProposal);
      return backendProposal;
    } catch (error) {
      if (requestSeqRef.current === requestSeq) {
        setProposalError(error instanceof Error ? error.message : "Codex proposal request failed");
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

export default App;
