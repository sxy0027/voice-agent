# ADR-017 Role-Based Slow LLM Orchestration and Dynamic Task Requirement Model

## Status

accepted

## Context

The first Slow-system Workbench slice used a fixed `customer_reception` schema and one Codex call that presented internal trace labels as if they were separate roles. That proved schema-backed clarification, but it cannot safely support unrelated planning tasks. Fixed field allowlists, meal keywords, itinerary arguments, and clarification templates also put business policy inside the generic adapter and session path.

The next slice needs dynamic task requirements and genuinely separate model roles without transferring state-machine ownership to a model. It must preserve ADR-002 replay, ADR-004 plan versioning, ADR-007 UserPatch evidence, ADR-008 SlowTask-led conflict resolution, ADR-009 SemanticCommitment ownership, and ADR-016 lifecycle and tool authorization.

## Decision

### Python owns orchestration

`SlowLLMRoleOrchestrator` is the only component that chooses role order. The allowed roles are `TASK_MODELER`, `REQUIREMENT_ANALYST`, `CLARIFIER`, `PLANNER`, and `REVIEWER`. A role cannot invoke another role, mutate task state, authorize or execute tools, advance `plan_version`, emit a SemanticCommitment, or patch UI state.

New tasks normally use Task Modeler, Requirement Analyst, and exactly one primary branch: Clarifier or Planner followed by Reviewer. Ordinary slot completion reuses the accepted model and does not rerun Task Modeler. Goal rewrites invalidate the model and may run Task Modeler again after the existing material UserPatch path advances the plan.

Invocation budgets are enforced by Python: at most four role calls for a new task, three for an ordinary UserPatch, and one bounded Planner repair. Roles have independent timeouts of at most 60 seconds and a bounded 180-second total orchestration deadline. Cache keys contain `task_id`, `plan_version`, context hash, role, and role contract version. There is no recursive role invocation.

### Dynamic Task Requirement Model

Task Modeler emits a proposal only. Python validates it against a strict, non-executable contract before SlowTask may accept it. An accepted model contains a stable ID and version, task kind and summary, components, requirements, success criteria, applicable stages, source proposal ref, and accepted context hash.

Requirement IDs use a safe token grammar and are unique. Requirement count and text lengths are bounded. Value types, stages, source routes, activation operators, and validation operators come from finite allowlists. Validation and activation are declarative data only; Python, expressions, templates, and executable code are forbidden. Tool bindings may only reference an existing ToolRegistry manifest and one of that manifest's declared arguments. Models may not expand a tool manifest or request secrets, tokens, passwords, credentials, or other sensitive user input.

SlowTask records acceptance with `TASK_REQUIREMENT_MODEL_ACCEPTED`. The event carries the validated, bounded model payload plus Python-owned `model_status`, `model_confidence`, `bootstrap_reason`, accepted context hash, and `needs_remodeling`, so deterministic replay can restore the accepted model lifecycle without calling a provider. A material goal rewrite records `TASK_REQUIREMENT_MODEL_INVALIDATED`; the old model cannot resolve requirements for the new goal. Model versioning is independent from, but bound to, the current `plan_version`.

Accepted model statuses are `PROVISIONAL_BOOTSTRAP`, `ACCEPTED_SPECIFIC`, and `DEGRADED`. A generic bootstrap is always provisional, low-confidence, and eligible for bounded remodeling. A specific validated provider model is accepted specific. A deterministic specific fallback after provider failure remains explicitly degraded and low-confidence; provider failure is never presented as a validated real model.

`customer_reception` remains a registered built-in profile and fake-provider fixture. It is never the global production fact owner. If Task Modeler fails, a deterministic fallback may select a registered fixture from bounded semantic signals in the current goal, latest user input, and accepted evidence summary. A matched specific fixture remains `DEGRADED`; no match produces `PROVISIONAL_BOOTSTRAP`. This fallback is isolated from the real-provider path, reports the provider reason code, and cannot expand tools or bypass model validation.

When an accepted model is provisional or degraded and a new task-like evidence context arrives, Python may remodel once for that context hash. It records `TASK_REQUIREMENT_MODEL_INVALIDATED`, accepts model version `n+1`, rebuilds context from the reducer, and then runs Requirement Analyst. This interpretation refinement does not advance `plan_version` by itself and does not delete accepted user evidence. The same context hash cannot recursively or repeatedly trigger remodeling.

### Requirement state ownership

Requirement Analyst proposes statuses, values, resolution routes, evidence refs, ambiguity/conflict information, blocking stage, and material-change hints. SlowTask/Python validates provenance, source route, accepted model membership, current plan binding, and stale-evidence policy before accepting updates into the requirement ledger.

`USER` gaps may be selected for clarification. `TOOL` gaps are handled by an information-gathering Planner and must not be presented as user questions. `DERIVED` and `SYSTEM` values require an accepted derivation or system source. `OPTIONAL` gaps never block readiness. Filling an unknown requirement keeps the current plan version; replacing an established value follows ADR-004 and ADR-016 and advances it through the existing UserPatch events.

### Role contracts

Every role has a distinct prompt, JSON schema, validator, fake fixture, timeout, and minimal context projection. All outputs use a common proposal envelope with role and task binding, input context hash, source evidence refs, confidence, public summary, payload, and boundary assertions. Boundary assertions must state no state mutation, plan advance, tool execution/authorization, SemanticCommitment, UI mutation, or chain-of-thought.

Clarifier receives backend-selected requirement IDs and cannot add or remove them. A mismatch records `ADAPTER_OUTPUT_VALIDATION_FAILED`, marks the proposal degraded, and uses a deterministic label-based fallback question.

Planner emits proposals only. Proposed tool calls are checked against ToolRegistry, argument declarations, provenance, current-plan evidence, and stale evidence. Planner can produce a no-tool plan. Tool Executor remains the only execution and authorization owner.

Reviewer returns `PASS`, `REVISE`, or `BLOCK` and cannot rewrite the plan. Python permits final SlowTask checks only after `PASS`. One bounded Planner repair is allowed after `REVISE`. Reviewer failure is treated as `BLOCK`; Planner or Reviewer failure cannot produce a SemanticCommitment.

### Replay and observability

Role invocations use existing adapter request, validation-failure, degraded, and structured-output events. Structured output records only validated proposal metadata, public summaries, reason codes, requirement statuses, evidence refs, and validation results. Prompt text, provider raw bodies, chain-of-thought, secrets, raw audio, and local schema paths are not journaled. The user-visible response is selected by Python for the current turn and is bound to its source role, proposal ID, input context hash, turn ID, and causing event; the frontend displays that conversation projection and does not infer an answer from the globally latest proposal.

Replay reads recorded role proposal references and `TASK_REQUIREMENT_MODEL_ACCEPTED`/`INVALIDATED` events. It never reruns Codex, tools, network, clocks, or randomness. TaskContextPack projects the accepted model, dynamic requirement summary, readiness, current role, prior proposal refs, tool manifest summaries, selected clarification IDs, Planner mode, plan proposal ref, and Reviewer status.

## Consequences

The generic control path can support unrelated task kinds without a universal field list or itinerary assumption. Model flexibility is bounded by Python validation and SlowTask ownership. The cost is more contracts, validation code, role calls, and replay metadata. Fake provider profiles remain deterministic test fixtures rather than production task policy.

## Validation Method

Tests must cover customer reception compatibility, research-report planning, code-refactor planning, team-event planning, USER versus TOOL gaps, Clarifier field mismatch, Planner tool/stale/unknown-fact violations, plan-version behavior, goal rewrite invalidation, deterministic replay, and invocation budgets. All Python tests use `./scripts/test`.
