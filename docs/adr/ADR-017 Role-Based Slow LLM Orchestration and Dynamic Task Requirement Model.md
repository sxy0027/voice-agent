# ADR-017 Role-Based Slow LLM Orchestration and Dynamic Task Requirement Model

## Status

accepted

## Context

The first Slow-system Workbench slice used a fixed `customer_reception` schema and one Codex call that presented internal trace labels as if they were separate roles. That proved schema-backed clarification, but it cannot safely support unrelated planning tasks. Fixed field allowlists, meal keywords, itinerary arguments, and clarification templates also put business policy inside the generic adapter and session path.

The next slice needs dynamic task requirements and genuinely separate model roles without transferring state-machine ownership to a model. It must preserve ADR-002 replay, ADR-004 plan versioning, ADR-007 UserPatch evidence, ADR-008 SlowTask-led conflict resolution, ADR-009 SemanticCommitment ownership, and ADR-016 lifecycle and tool authorization.

## Decision

### Python owns orchestration

`SlowLLMRoleOrchestrator` is the only component that chooses role order. The allowed roles are `TASK_MODELER`, `REQUIREMENT_ANALYST`, `CLARIFIER`, `PLANNER`, and `REVIEWER`. A role cannot invoke another role, mutate task state, authorize or execute tools, advance `plan_version`, emit a SemanticCommitment, or patch UI state.

New tasks normally use Task Modeler and then the minimum required downstream branch. Registered profiles may use their Python extractor and deterministic Clarifier without invoking Requirement Analyst or Clarifier providers. Ordinary slot completion reuses the accepted model and does not rerun Task Modeler. Goal rewrites invalidate the model and may run Task Modeler again after the existing material UserPatch path advances the plan.

Invocation budgets are enforced by Python. Deterministic extraction, clarification, tool compilation, and read-only tool start consume no provider budget. Semantic Planner repair has its own bounded budget and does not depend on unused capacity in the normal invocation list. Role defaults are Task Modeler `medium`, Requirement Analyst `low`, Planner `medium`, Reviewer `low`, with fail-fast timeouts no greater than 30 seconds per call and a bounded 60-second orchestration deadline. Cache keys contain `task_id`, `plan_version`, context hash, role, and role contract version. There is no recursive role invocation.

### Dynamic Task Requirement Model

Task Modeler emits a proposal only. Python validates it against a strict, non-executable contract before SlowTask may accept it. An accepted model contains a stable ID and version, task kind and summary, components, requirements, success criteria, applicable stages, source proposal ref, and accepted context hash.

Requirement IDs use a safe token grammar and are unique. Requirement count and text lengths are bounded. Value types, stages, source routes, activation operators, and validation operators come from finite allowlists. Validation and activation are declarative data only; Python, expressions, templates, and executable code are forbidden. Tool bindings may only reference an existing ToolRegistry manifest and one of that manifest's declared arguments. Models may not expand a tool manifest or request secrets, tokens, passwords, credentials, or other sensitive user input.

SlowTask records acceptance with `TASK_REQUIREMENT_MODEL_ACCEPTED`. The event carries the validated, bounded model payload plus Python-owned `model_status`, `model_confidence`, `bootstrap_reason`, accepted context hash, and `needs_remodeling`, so deterministic replay can restore the accepted model lifecycle without calling a provider. A material goal rewrite records `TASK_REQUIREMENT_MODEL_INVALIDATED`; the old model cannot resolve requirements for the new goal. Model versioning is independent from, but bound to, the current `plan_version`.

Accepted model statuses are `PROVISIONAL_BOOTSTRAP`, `ACCEPTED_SPECIFIC`, and `DEGRADED`. A generic bootstrap is always provisional, low-confidence, and eligible for bounded remodeling. A specific validated provider model is accepted specific. A deterministic specific fallback after provider failure remains explicitly degraded and low-confidence; provider failure is never presented as a validated real model.

`customer_reception` remains a registered built-in profile and fake-provider fixture. It is never the global production fact owner. If Task Modeler fails, a deterministic fallback may select a registered fixture from bounded semantic signals in the current goal, latest user input, and accepted evidence summary. A matched specific fixture remains `DEGRADED`; no match produces `PROVISIONAL_BOOTSTRAP`. This fallback is isolated from the real-provider path, reports the provider reason code, and cannot expand tools or bypass model validation.

When an accepted model is provisional or degraded and a new task-like evidence context arrives, Python may remodel once for that context hash. It records `TASK_REQUIREMENT_MODEL_INVALIDATED`, accepts model version `n+1`, rebuilds context from the reducer, and then runs Requirement Analyst. This interpretation refinement does not advance `plan_version` by itself and does not delete accepted user evidence. The same context hash cannot recursively or repeatedly trigger remodeling.

### Requirement state ownership

Requirement Analyst proposes statuses, values, resolution routes, evidence refs, ambiguity/conflict information, blocking stage, and material-change hints. SlowTask/Python validates provenance, source route, accepted model membership, current plan binding, and stale-evidence policy before accepting updates into the requirement ledger.

`USER` gaps may be selected for clarification. `TOOL` gaps must not be presented as user questions. A Python-owned `ToolGapResolver` maps active TOOL gaps to registered, declarative `AllowedToolContract` entries. A Python-owned `ToolArgumentCompiler` invokes only allowlisted resolver IDs, rebuilds arguments from current non-stale accepted facts, aggregates their provenance, and validates the result against the selected ToolRegistry manifest. Missing bindings retain a partial no-tool plan; models cannot choose an unrelated manifest. `DERIVED` and `SYSTEM` values require an accepted derivation or system source. `OPTIONAL` gaps never block readiness. Filling an unknown requirement keeps the current plan version; replacing an established value follows ADR-004 and ADR-016 and advances it through the existing UserPatch events.

### Role contracts

Every role has a distinct prompt, JSON schema, validator, fake fixture, timeout, and minimal context projection. All outputs use a common proposal envelope with role and task binding, input context hash, source evidence refs, confidence, public summary, payload, and boundary assertions. Boundary assertions must state no state mutation, plan advance, tool execution/authorization, SemanticCommitment, UI mutation, or chain-of-thought.

Clarifier fields are selected by Python. The default interactive realization is a deterministic label-based renderer and makes no provider call. Optional model polishing cannot own selected fields or block the reply.

Planner emits semantic plans and high-level `ToolIntentProposal` objects only. An intent can select a `binding_id` from the current active-gap `allowed_tool_contracts`; it cannot author low-level arguments or provenance. A legacy raw proposed tool call is diagnostic input only and is always ignored or rebuilt by the compiler. For low-risk read-only information gathering, a `ToolValidationReport.PASS` can proceed directly to Tool Executor before Planner runs. Planner normally runs once after the ToolResult is available. Tool Executor remains the only execution and authorization owner.

Reviewer returns `PASS`, `REVISE`, or `BLOCK` and cannot rewrite the plan. It reviews semantic coverage, unsupported claims, stale evidence, premature commitment, and risk. It does not own manifest argument names or low-level binding validity. A tool objection must reference an existing non-PASS Python report and one of that report's reason codes; an objection that contradicts `PASS` is an adapter output validation failure and cannot block a read-only start. Python permits final SlowTask checks only after a valid Reviewer `PASS`. Reviewer failure can block SemanticCommitment, but cannot retroactively relabel or block a deterministically validated read-only search.

### Deterministic tool validation report

Every compiled tool candidate receives a Python-owned `ToolValidationReport` with status `PASS`, `REPAIRABLE`, or `BLOCKED`; error source, task and plan binding, tool and binding IDs, resolved requirements, required/provided/unknown/missing/invalid arguments, missing provenance, binding errors, bounded reason codes, recoverability, and causing proposal reference. The shareable projection contains argument names and a fingerprint, never argument values. Compiler repair records what diagnostic candidate shape was rejected and proves that execution arguments were rebuilt from accepted facts.

The report, registered binding metadata, accepted-fact provenance metadata, and compiled argument fingerprint are recorded inside the existing canonical `ARGUMENTS_RESOLVED` payload. Reducer replay restores them without calling a provider, resolver, tool, clock, or network. No new MVP event name is introduced by this hardening.

Tool validation and Reviewer diagnostics are distinct. User-visible parameter-failure language may be derived only from a non-PASS Python report or canonical Tool Executor events such as `TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS`; Reviewer free text alone cannot claim that Tool Executor validated or rejected arguments.

### Replay and observability

Role invocations use existing adapter request, validation-failure, degraded, and structured-output events. Structured output records only validated proposal metadata, public summaries, reason codes, requirement statuses, evidence refs, and validation results. Prompt text, provider raw bodies, chain-of-thought, secrets, raw audio, and local schema paths are not journaled. The user-visible response is selected by Python for the current turn and is bound to its source role, proposal ID, input context hash, turn ID, and causing event; the frontend displays that conversation projection and does not infer an answer from the globally latest proposal.

Replay reads recorded role proposal references, `TASK_REQUIREMENT_MODEL_ACCEPTED`/`INVALIDATED`, and deterministic validation metadata carried by canonical events. It never reruns Codex, tools, network, clocks, or randomness. TaskContextPack projects the accepted model, dynamic requirement summary, readiness, current role, prior proposal refs, allowed tool contracts, selected clarification IDs, Planner mode, plan proposal ref, Reviewer status, latest ToolValidationReport, binding, argument fingerprint, and non-stale provenance refs.

Read-only external lookup may run in a tracked background task after the foreground request returns `tool_running`. The task is bound to `session_id`, `task_id`, `plan_version`, and reset generation; reset cancels it, completion rechecks the binding under the session lock, exceptions are consumed into bounded type-only diagnostics, and late prior-plan results follow ADR-016 stale handling. Safe observability may expose stage names and millisecond durations for turn, role, subprocess startup, compilation, Tool Executor start, and external lookup, but not prompt text, provider bodies, or argument values.

## Consequences

The generic control path can support unrelated task kinds without a universal field list or itinerary assumption. Model flexibility is bounded by Python validation and SlowTask ownership. The cost is more contracts, validation code, role calls, and replay metadata. Fake provider profiles remain deterministic test fixtures rather than production task policy.

## Validation Method

Tests must cover customer reception compatibility, research-report planning, code-refactor planning, team-event planning, USER versus TOOL gaps, Clarifier field mismatch, Planner tool/stale/unknown-fact violations, plan-version behavior, goal rewrite invalidation, deterministic replay, and invocation budgets. All Python tests use `./scripts/test`.
