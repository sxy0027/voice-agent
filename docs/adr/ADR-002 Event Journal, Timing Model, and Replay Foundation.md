# ADR-002 Event Journal, Timing Model, and Replay Foundation

## Status

accepted

## Context

MVP-0 的第一目标不是完整复杂任务能力，而是证明 event-driven live loop、interrupt、trace/replay 和模块边界。系统里会同时存在用户音频输入、Talker 播放、Duplex 判停/打断、Interaction state transition、ASR/Thinker 输出、Router 决策、SlowTask plan_version、ToolCall/ToolResult 等事件。

如果没有统一的事件顺序和时间模型，就无法回答这些关键问题：

- 用户什么时候开始说话、什么时候结束？
- 用户打断发生在 Talker 播放到哪一刻？
- 哪个 audio span 被提交给 ASR / Thinker？
- 某个 UserPatch 属于哪个 turn、哪个 task、哪个 plan_version？
- 某个 ToolResult 是否来自旧 plan_version？
- replay 时如何重建 InteractionState、TaskFocusState、SlowTask 状态？

同时，event journal 不能被设计成所有模块强同步等待的中央阻塞总线，否则会破坏 live 语音系统的低延迟目标。

## Decision

引入 per-session append-only event journal，作为 trace、replay、因果关系、interrupt、plan_version consistency 的基础。

Event journal 的定位：

- 每个 session 一个 append-only journal。
- journal 是事实记录和因果索引，不是全局阻塞消息总线。
- Runtime 可以先内存 append，再异步持久化。
- 模块可以基于事件流、状态快照或直接调用协作，但所有关键状态迁移必须最终落成 event。
- replay 默认基于已记录事件重建状态，不重新调用外部模型或工具，除非显式进入 re-eval 模式。

最小 event envelope 字段：

- `event_id`
- `event_seq`
- `event_schema_version`
- `session_id`
- `conversation_id`
- `source_module`
- `created_monotonic_ms`
- `created_wall_clock_ms`
- `caused_by_event_id`
- `supersedes_event_id` optional
- `trace_redaction_level`

常用 context binding 字段按事件需要携带：

- `turn_id`
- `utterance_id`
- `input_span_id`
- `text_span_id`
- `audio_span_id`
- `playback_span_id`
- `task_id`
- `plan_version`
- `task_event_seq`
- `audio_sample_offset`
- `playback_offset_ms`

字段语义：

- `event_id`: 全局唯一事件 ID。
- `event_seq`: session 内严格递增序号，由 session runtime 分配。
- `event_schema_version`: 事件 envelope / payload schema 版本，用于 replay fixture 和后续 migration。
- `source_module`: 产生该事件的模块 owner，例如 access_layer、duplex、interaction_controller、router、talker。
- `created_monotonic_ms`: session runtime 单调时间，用于排序和 latency 计算。
- `created_wall_clock_ms`: 真实墙钟时间，只用于展示、审计、跨系统粗略对齐，不作为严格排序依据。
- `audio_sample_offset`: 用户输入音频流中的 sample offset。
- `playback_offset_ms`: Talker 输出播放进度，用于 truncate 和 playback delivery marker。
- `caused_by_event_id`: 表示因果来源。
- `supersedes_event_id`: 表示当前事件替代或废弃之前事件，例如新 plan_version 废弃旧 planning result。
- `trace_redaction_level`: 表示 payload 的 trace/redaction 等级，例如 local_debug、redacted_fixture、metadata_only。
- `plan_version`: 仅对 SlowTask、ToolCall、ToolResult、UserPatch、SemanticCommitment 等任务相关事件有意义；无关事件可为空。

Timing model 采用三层时间：

1. `event_seq`
   session 内逻辑顺序，用于 replay 主排序。

2. `created_monotonic_ms`
   单调时间，用于 latency、SLO、相对时序判断。

3. audio / playback offsets
   用于音频事实判断，尤其是 barge-in、truncate、echo overlap、用户是否可能听到某段回复。

## Canonical MVP-0 Event Registry

ADR-002 是 MVP-0 事件命名的唯一来源。后续 ADR 若引用事件名，必须以本节为准。

所有 canonical events 必须包含上文的最小 event envelope 字段。下表的 required_fields 只列 event-specific payload 和 context binding 字段。

### Naming rules

- `BARGE_IN_CANDIDATE` 表示用户输入与助手 playback overlap，由 Duplex / Realtime Audio Controller 产生。
- `INTERRUPT_CANDIDATE` 表示 Interaction Controller 基于 barge-in、confidence、playback state、policy 后形成的中断候选。
- `TTS_TRUNCATE_REQUESTED` 表示系统请求 Talker 截断播放，由 Interaction Controller 产生。
- `TTS_TRUNCATED` 表示 Talker 确认截断已生效。
- `PLAYBACK_COMMITTED` means the system believes audio up to `playback_offset_ms` has likely been emitted to the user-facing playback device. It is not a guarantee that the user cognitively heard or understood it. It is a playback delivery marker, not a semantic acknowledgement marker.

Text input ingress policy for event journal:

- Text input 不经过 Duplex，但不能绕过 Interaction Controller。
- Text path is: Access Layer -> `TEXT_INPUT_RECEIVED` -> Interaction Controller -> `TURN_OPENED` -> `TURN_INGRESS_ACCEPTED` -> `TURN_INGRESS_COMMITTED`.
- Text input 不生成 synthetic `audio_span_id`; `audio_span_id=null`.
- Text input 使用 `input_span_id` 或 `text_span_id`, with `input_modality=text`.
- Text input records `directedness=ASSUMED_DIRECTED` and `semantic_close=ASSUMED_CLOSED`.
- If text input arrives during assistant playback, interruption is decided by Interaction Controller policy and recorded as `INTERRUPT_CANDIDATE` / `TTS_TRUNCATE_REQUESTED` if it proceeds.

| event family | event_name | payload_owner | required_fields | causal_links | required_in_MVP_0 |
| --- | --- | --- | --- | --- | --- |
| Session events | `SESSION_STARTED` | Session Runtime | `session_id`, `conversation_id`, `runtime_config_ref`, `capability_snapshot_ref` | root event | true |
| Session events | `SESSION_ENDED` | Session Runtime | `session_id`, `end_reason` | caused by final runtime/user/system event | false |
| Session events | `ADAPTER_CAPABILITY_SNAPSHOT_RECORDED` | Adapter Registry / Session Runtime | `capability_snapshot_ref`, `adapter_ids`, `adapter_types`, `deployment_modes`, `output_modes` | caused by session startup or adapter registry refresh | true |
| Input events | `TEXT_INPUT_RECEIVED` | Access Layer | `input_span_id`, `text_span_id`, `input_modality=text`, `redacted_text` or `text_ref`, `directedness=ASSUMED_DIRECTED`, `semantic_close=ASSUMED_CLOSED` | caused by user text ingress | true when text input is used |
| Input events | `LOW_CONFIDENCE_INGRESS` | Interaction Controller | `input_span_id` or `audio_span_id`, `confidence_fields`, `ingress_reason` | caused by Duplex candidate or text policy check | true when low-confidence path is exercised |
| Audio span events | `AUDIO_SPAN_STARTED` | Access Layer | `audio_span_id`, `input_modality=audio`, `audio_sample_offset`, `audio_format_ref` | caused by mic/audio ingress | true for audio path |
| Audio span events | `AUDIO_CHUNK_RECEIVED` | Access Layer | `audio_span_id`, `chunk_index`, `audio_sample_offset`, `chunk_duration_ms` | caused by `AUDIO_SPAN_STARTED` | false |
| Audio span events | `AUDIO_SPAN_ENDED` | Access Layer | `audio_span_id`, `audio_sample_offset`, `duration_ms`, `end_reason` | caused by audio ingress end | true for audio path |
| Playback events | `PLAYBACK_SPAN_STARTED` | Talker | `playback_span_id`, `spoken_plan_id` optional, `approved_check_event_id` optional, `audio_ref` or `tts_stream_ref` | caused by approved response / SpokenPlan after required coverage or truthfulness check has passed | true |
| Playback events | `PLAYBACK_PROGRESS` | Talker | `playback_span_id`, `playback_offset_ms` | caused by `PLAYBACK_SPAN_STARTED` | true |
| Playback events | `PLAYBACK_COMMITTED` | Talker | `playback_span_id`, `playback_offset_ms`, `commit_basis` | caused by playback progress or playback finish | true |
| Playback events | `PLAYBACK_FINISHED` | Talker | `playback_span_id`, `final_playback_offset_ms` | caused by playback completion | false |
| Playback events | `TTS_TRUNCATE_REQUESTED` | Interaction Controller | `playback_span_id`, `cutoff_playback_offset_ms`, `interrupt_candidate_event_id` | caused by `INTERRUPT_CANDIDATE` | true for interrupt path |
| Playback events | `TTS_TRUNCATED` | Talker | `playback_span_id`, `actual_stop_offset_ms`, `truncate_request_event_id` | caused by `TTS_TRUNCATE_REQUESTED` | true for interrupt path |
| Duplex events | `SPEECH_START_DETECTED` | Duplex / Realtime Audio Controller | `audio_span_id`, `audio_sample_offset`, `vad_confidence` | caused by `AUDIO_SPAN_STARTED` or audio chunk analysis | true for audio path |
| Duplex events | `SPEECH_END_DETECTED` | Duplex / Realtime Audio Controller | `audio_span_id`, `audio_sample_offset`, `vad_confidence`, `silence_duration_ms` | caused by audio chunk analysis / `AUDIO_SPAN_ENDED` | true for audio path |
| Duplex events | `BARGE_IN_CANDIDATE` | Duplex / Realtime Audio Controller | `audio_span_id`, `playback_span_id`, `playback_offset_ms`, `echo_likelihood`, `vad_confidence`, `barge_in_confidence` | caused by speech/playback overlap | true for interrupt path |
| Duplex events | `DIRECTEDNESS_CANDIDATE` | Duplex / Realtime Audio Controller | `audio_span_id`, `directedness`, `directedness_confidence` | caused by audio analysis | false |
| Duplex events | `SEMANTIC_CLOSE_CANDIDATE` | Duplex / Realtime Audio Controller | `audio_span_id`, `semantic_close`, `semantic_close_confidence` | caused by audio analysis | false |
| Duplex events | `NON_ASSISTANT_CANDIDATE` | Duplex / Realtime Audio Controller | `audio_span_id`, `directedness=NOT_DIRECTED`, `directedness_confidence` | caused by directedness analysis | false |
| Interaction events | `TURN_OPENED` | Interaction Controller | `turn_id`, `input_span_id` or `audio_span_id`, `turn_phase=COLLECTING_INPUT`, `input_modality` | caused by `TEXT_INPUT_RECEIVED` or `SPEECH_START_DETECTED` | true |
| Interaction events | `TURN_HELD` | Interaction Controller | `turn_id`, `ingress_outcome=HELD`, `semantic_close`, `directedness` | caused by `SEMANTIC_CLOSE_CANDIDATE` or policy | true when hold path is exercised |
| Interaction events | `TURN_INGRESS_ACCEPTED` | Interaction Controller | `turn_id`, `input_span_id` or `audio_span_id`, `ingress_outcome=ACCEPTED` | caused by text ingress or Duplex accepted candidate | true |
| Interaction events | `TURN_INGRESS_REJECTED` | Interaction Controller | `turn_id`, `input_span_id` or `audio_span_id`, `ingress_outcome=REJECTED`, `reject_reason` | caused by `NON_ASSISTANT_CANDIDATE` or low-confidence policy | true when reject path is exercised |
| Interaction events | `TURN_INGRESS_COMMITTED` | Interaction Controller | `turn_id`, `utterance_id`, `input_modality`, `input_span_id` or `text_span_id` or `audio_span_id`, `directedness`, `semantic_close`, `ingress_outcome=COMMITTED` | caused by `TURN_INGRESS_ACCEPTED` or text policy | true |
| Interaction events | `INTERRUPT_CANDIDATE` | Interaction Controller | `playback_span_id`, `audio_span_id` optional, `playback_offset_ms`, `policy_reason`, `confidence_summary` | caused by `BARGE_IN_CANDIDATE` or text-during-playback policy | true for interrupt path |
| Interaction events | `WAITING_USER` | Interaction Controller | `turn_id` optional, `wait_reason` | caused by response end, held input, clarification, or confirmation state | false |
| Router events | `ROUTER_DECISION_EMITTED` | Router | `turn_id`, `utterance_id`, `router_decision`, `task_focus` optional, `confidence` | caused by `TURN_INGRESS_COMMITTED` and ASR/Thinker frames if available | true |
| Router events | `TASK_FOCUS_STATE_UPDATED` | Router | `active_task_id` optional, `foreground_mode`, `side_conversation_allowed`, `default_patch_policy`, `ambiguous_input_policy`, `last_focus_decision`, `last_focus_confidence`, `router_decision_event_id` | caused by `ROUTER_DECISION_EMITTED` when TaskFocusState changes or must be snapshotted for replay | false |
| Model adapter events | `ADAPTER_HEALTHCHECK_FAILED` | Adapter Registry / Adapter | `adapter_id`, `adapter_type`, `health_status`, `failure_reason`, `output_mode` | caused by adapter startup or periodic healthcheck | false |
| Model adapter events | `ADAPTER_REQUEST_RETRYING` | Adapter | `adapter_id`, `adapter_type`, `adapter_request_id`, `retry_count`, `retry_reason`, `timeout_ms` optional | caused by retryable adapter failure or timeout | false |
| Model adapter events | `ADAPTER_REQUEST_FAILED` | Adapter | `adapter_id`, `adapter_type`, `adapter_request_id`, `failure_reason`, `retryable`, `timeout_ms` optional, `output_mode` | caused by adapter request failure after policy handling | false |
| Model adapter events | `ADAPTER_OUTPUT_VALIDATION_FAILED` | Adapter / Schema Validator | `adapter_id`, `adapter_type`, `adapter_request_id`, `schema_name`, `failure_reasons`, `output_mode` | caused by model/provider output failing system schema validation | false |
| Model adapter events | `ADAPTER_OUTPUT_DEGRADED` | Adapter / Runtime | `adapter_id`, `adapter_type`, `adapter_request_id` optional, `degraded_reason`, `missing_capability` optional, `fallback_adapter_id` optional, `output_mode` | caused by unsupported capability, fallback, or degraded provider output | false |
| Model adapter events | `ASR_TRANSCRIPT_OUTPUT_EMITTED` | ASR Adapter | `adapter_id`, `adapter_type=asr`, `adapter_request_id`, `turn_id`, `utterance_id`, `input_modality=audio`, `audio_span_id`, `asr_frame_ref`, `text_ref`, `transcript_finality=final`, `timestamp_status`, `streaming_status`, `output_mode=real|fallback|degraded` | caused by `TURN_INGRESS_COMMITTED`; missing timestamp or streaming capability must be paired with `ADAPTER_OUTPUT_DEGRADED` | false |
| Model adapter events | `THINKER_SEMANTIC_FRAME_OUTPUT_EMITTED` | Thinker Adapter | `adapter_id`, `adapter_type=thinker`, `adapter_request_id`, `turn_id`, `utterance_id`, `input_modality`, `semantic_frame_schema`, `normalization_status=normalized`, `semantic_frame_ref`, `semantic_summary_ref`, `semantic_close_status`, `assistant_directedness_status`, `emotion_status`, `audio_caption_status`, `output_mode=real|fallback|degraded` | caused by `TURN_INGRESS_COMMITTED`; missing semantic close, assistant-directedness, emotion, or audio caption must be paired with `ADAPTER_OUTPUT_DEGRADED` and must not be silently defaulted | false |
| Model adapter events | `SLOW_LLM_STRUCTURED_OUTPUT_EMITTED` | Slow LLM Adapter | `adapter_id`, `adapter_type=slow_llm`, `adapter_request_id`, `task_id`, `plan_version`, `task_event_seq`, `schema_name=voice_agent.slowtask.structured_output.v1`, `normalization_status=normalized`, `slow_llm_output_ref`, `structured_output_ref`, `validation_result_ref`, `output_mode=real|fallback|degraded` | caused by the current SlowTask event that requested structured model output; SlowTask may consume only validated normalized refs/metadata, never provider-specific schema or raw payload | false |
| Model adapter events | `TTS_SYNTHESIS_OUTPUT_EMITTED` | TTS Adapter | `adapter_id`, `adapter_type=tts`, `adapter_request_id`, `spoken_plan_id`, `approved_check_event_id`, `normalization_status=normalized`, `audio_ref` or `tts_stream_ref`, `audio_format_ref`, `synthesis_result_ref`, `truncate_status=supported|unsupported_blocked`, `output_mode=real|fallback|degraded` | caused by the passed SpokenPlan check that approved playback; MVP-3 playback must link to a prior TTS output by `tts_output_event_id` or unique safe ref match; playback may consume only safe audio refs/metadata, never raw audio bytes or provider payload; unsupported truncate must be paired with `ADAPTER_OUTPUT_DEGRADED` and must block barge-in truncate target validation | false |
| Model adapter events | `MOCK_ASR_FRAME_EMITTED` | ASR Adapter | `turn_id`, `utterance_id`, `input_modality`, `asr_frame_ref`, `output_mode=mock` | caused by `TURN_INGRESS_COMMITTED` when ASR mock is used | true |
| Model adapter events | `MOCK_THINKER_FRAME_EMITTED` | Thinker Adapter | `turn_id`, `utterance_id`, `semantic_frame_ref`, `output_mode=mock` | caused by `TURN_INGRESS_COMMITTED` when Thinker mock is used | true |
| SlowTask events | `SLOWTASK_CREATED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `initial_goal_ref` | caused by `ROUTER_DECISION_EMITTED` with spawn decision | false |
| SlowTask events | `SLOWTASK_STATE_CHANGED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `from_state`, `to_state`, `reason` | caused by SlowTask internal event | false |
| SlowTask events | `USER_PATCH_RECEIVED` | UserPatch Pipeline | `patch_id`, `task_id`, `plan_version`, `task_event_seq`, `observed_plan_version`, `evidence_ref` | caused by `ROUTER_DECISION_EMITTED` with patch decision | false |
| SlowTask events | `USER_PATCH_INTERPRETED` | SlowTask Runtime | `patch_id`, `task_id`, `interpreted_against_plan_version`, `interpretation_type`, `materially_changes_task` | caused by `USER_PATCH_RECEIVED` | false |
| SlowTask events | `PLAN_VERSION_ADVANCED` | SlowTask Runtime | `task_id`, `from_plan_version`, `to_plan_version`, `planning_reason`, `caused_by_user_patch_event_id` optional | caused by material patch/tool/risk change | false |
| SlowTask events | `TASK_REPLANNED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `planning_reason`, `superseded_plan_version` optional | caused by `PLAN_VERSION_ADVANCED` or initial planning | false |
| SlowTask events | `TASK_REQUIREMENT_MODEL_ACCEPTED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `model_id`, `model_version`, `task_kind`, `model_ref`, `source_proposal_ref`, `accepted_context_hash`, `model_status`, `model_confidence`, `bootstrap_reason`, `needs_remodeling`, `model_payload` | caused by a validated Task Modeler proposal or an explicitly degraded deterministic fallback | false |
| SlowTask events | `TASK_REQUIREMENT_MODEL_INVALIDATED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `model_ref`, `invalidation_reason` | caused by a material goal rewrite or accepted model replacement | false |
| SlowTask evidence events | `EVIDENCE_REVIEWED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `evidence_refs`, `review_result` | caused by SlowTask review of ASR / Thinker / UserPatch / tool evidence | false |
| SlowTask evidence events | `AMBIGUITY_DETECTED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `ambiguous_fields`, `source_evidence_refs` | caused by `EVIDENCE_REVIEWED` | false |
| SlowTask evidence events | `AMBIGUITY_RESOLVED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `resolved_fields`, `resolution_reason`, `source_evidence_refs` | caused by `AMBIGUITY_DETECTED` or `EVIDENCE_REVIEWED` | false |
| SlowTask evidence events | `CLARIFICATION_REQUESTED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `missing_or_ambiguous_fields`, `clarification_prompt_ref` | caused by `AMBIGUITY_DETECTED` or `INSUFFICIENT_EVIDENCE_FOR_ACTION` | false |
| SlowTask evidence events | `ARGUMENTS_RESOLVED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `resolved_arguments_ref`, `provenance_ref` | caused by `EVIDENCE_REVIEWED` or `AMBIGUITY_RESOLVED` | false |
| SlowTask evidence events | `ARGUMENT_RESOLUTION_PROVENANCE` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `field_provenance_refs` | caused by `ARGUMENTS_RESOLVED` | false |
| SlowTask evidence events | `INSUFFICIENT_EVIDENCE_FOR_ACTION` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `blocking_fields`, `source_evidence_refs` | caused by evidence review before action / tool execution | false |
| SlowTask progress events | `PLANNING_STARTED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `planning_reason` | caused by `SLOWTASK_CREATED` or `TASK_REPLANNED` | false |
| SlowTask progress events | `PLANNING_RESTARTED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `restart_reason` | caused by `PLAN_VERSION_ADVANCED` or tool/risk change | false |
| SlowTask progress events | `WAITING_FOR_SLOT` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `missing_fields` | caused by `CLARIFICATION_REQUESTED` | false |
| SlowTask progress events | `WAITING_FOR_TOOL` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `tool_call_id` | caused by `TOOL_EXECUTION_STARTED` | false |
| SlowTask progress events | `WAITING_FOR_USER_CONFIRMATION` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `confirmation_id` | caused by `CONFIRMATION_REQUIRED` | false |
| SlowTask progress events | `FINALIZING` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `source_events` | caused by resolved arguments / tool result before commitment | false |
| SlowTask progress events | `SLOWTASK_DEGRADED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `degraded_reason`, `capability_or_tool_ref` optional | caused by adapter/tool failure or unavailable capability | false |
| SlowTask progress events | `SLOWTASK_FAILED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `failure_reason` | caused by unrecoverable task/tool/model failure | false |
| Confirmation events | `CONFIRMATION_REQUIRED` | SlowTask Runtime | `confirmation_id`, `task_id`, `plan_version`, `task_event_seq`, `confirmation_scope`, `required_for_event_id`, `prompt_ref` | caused by risky semantic change, demo destructive action, or user-facing authorization need | false |
| Confirmation events | `USER_CONFIRMATION_RECEIVED` | SlowTask Runtime | `confirmation_id`, `patch_id`, `task_id`, `plan_version`, `task_event_seq`, `confirmation_signal` | caused by `USER_PATCH_INTERPRETED` with confirmation / rejection / cancel | false |
| Confirmation events | `CONFIRMATION_ACCEPTED` | SlowTask Runtime | `confirmation_id`, `task_id`, `plan_version`, `task_event_seq`, `accepted_scope`, `authorization_ref` | caused by `USER_CONFIRMATION_RECEIVED` | false |
| Confirmation events | `CONFIRMATION_REJECTED` | SlowTask Runtime | `confirmation_id`, `task_id`, `plan_version`, `task_event_seq`, `rejection_reason` | caused by `USER_CONFIRMATION_RECEIVED` or timeout/cancel interpretation | false |
| Cancellation events | `SLOWTASK_CANCEL_REQUESTED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `cancel_reason`, `source_user_patch_event_id` optional | caused by `USER_PATCH_INTERPRETED` with cancel/control semantics | false |
| Cancellation events | `SLOWTASK_CANCELLED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `cancel_reason`, `inflight_tool_policy` | caused by accepted cancel request and required cleanup/cancel events | false |
| Cancellation events | `TOOL_EXECUTION_CANCEL_REQUESTED` | SlowTask Runtime | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `cancel_reason` | caused by plan advance or task cancellation when adapter supports cancellation | false |
| Tool events | `TOOL_CALL_STARTED` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `tool_name`, `idempotency_key` | MVP-1 minimal summary marker caused by current-plan SlowTask action; MVP-2 execution uses `TOOL_EXECUTION_STARTED` | false |
| Tool events | `TOOL_MANIFEST_LOADED` | Tool Executor | `tool_name`, `tool_adapter_id`, `tool_manifest_version`, `side_effect_class` | caused by tool preparation | false |
| Tool events | `TOOL_ARGUMENTS_PARTIAL` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `partial_arguments_ref`, `missing_fields` | caused by SlowTask proposed tool call with incomplete arguments | false |
| Tool events | `TOOL_ARGUMENTS_READY` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `resolved_arguments_ref`, `provenance_ref` | caused by `ARGUMENTS_RESOLVED` or validated current-plan tool request | false |
| Tool events | `TOOL_PREVIEW_AVAILABLE` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `preview_ref`, `requires_confirmation` | caused by ready arguments for previewable action | false |
| Tool events | `TOOL_EXECUTION_AUTHORIZED` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `authorization_basis`, `confirmation_id` optional | caused by policy allow or `CONFIRMATION_ACCEPTED` | false |
| Tool events | `TOOL_EXECUTION_STARTED` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `idempotency_key`, `authorization_event_id` optional | caused by `TOOL_EXECUTION_AUTHORIZED` or allowed read-only action | false |
| Tool events | `TOOL_PROGRESS_UPDATED` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `progress_type`, `progress_ref` | caused by in-flight tool execution | false |
| Tool events | `TOOL_UI_STATE_PATCHED` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `ui_patch_id`, `idempotency_key`, `patch_ref` | caused by demo backend/UI state mutation | false |
| Tool events | `TOOL_RESULT_RECEIVED` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `result_status`, `result_ref` | caused by tool execution completion | false |
| Tool events | `TOOL_EXECUTION_FAILED` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `failure_reason`, `retryable` | caused by tool execution failure | false |
| Tool events | `TOOL_CALL_RETRYING` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `retry_count`, `retry_reason` | caused by retryable `TOOL_EXECUTION_FAILED` | false |
| Tool events | `TOOL_EXECUTION_CANCELLED` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `cancel_request_event_id`, `cancel_status` | caused by `TOOL_EXECUTION_CANCEL_REQUESTED` | false |
| Tool events | `TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS` | Tool Executor | `tool_call_id`, `task_id`, `plan_version`, `task_event_seq`, `blocking_fields`, `source_event_id` | caused by missing resolved arguments or ambiguous critical fields | false |
| Tool events | `TOOL_RESULT_MARKED_STALE` | SlowTask Runtime | `tool_call_id`, `task_id`, `result_plan_version`, `current_plan_version`, `stale_reason` | caused by `TOOL_RESULT_RECEIVED` from old plan | false |
| Tool events | `STALE_EVIDENCE_RECORDED` | SlowTask Runtime | `task_id`, `stale_evidence_ref`, `source_tool_result_event_id` | caused by `TOOL_RESULT_MARKED_STALE` | false |
| Tool events | `STALE_EVIDENCE_ADOPTED` | SlowTask Runtime | `task_id`, `plan_version`, `task_event_seq`, `stale_evidence_ref`, `source_tool_result_event_id`, `adopted_from_plan_version`, `adoption_mode=adopt_or_rebase`, `adoption_reason`, `adopted_scope`, `adopted_by_event_id` | caused by explicit current-plan SlowTask adoption or rebase of stale evidence | false |
| Commitment / Composer events | `SEMANTIC_COMMITMENT_EMITTED` | SlowTask Runtime | `commitment_id`, `task_id`, `plan_version`, `task_event_seq`, `source_events` | caused by current-plan SlowTask completion/confirmation state | false |
| Commitment / Composer events | `SPOKEN_PLAN_EMITTED` | Composer | `spoken_plan_id`, `source_commitment_id` optional, `source_progress_event_ids`, `coverage_check_required`, `truthfulness_check_required` optional | caused by SemanticCommitment, progress event, or fast-system output | false |
| Commitment / Composer events | `COMMITMENT_COVERAGE_CHECK_PASSED` | Coverage Checker | `spoken_plan_id`, `source_commitment_id`, `checked_fields`, `check_result_ref` | caused by `SPOKEN_PLAN_EMITTED` for SemanticCommitment-derived speech | false |
| Commitment / Composer events | `COMMITMENT_COVERAGE_CHECK_FAILED` | Coverage Checker | `spoken_plan_id`, `source_commitment_id`, `failure_reasons` | caused by `SPOKEN_PLAN_EMITTED` | false |
| Commitment / Composer events | `PROGRESS_TRUTHFULNESS_CHECK_PASSED` | Coverage Checker / ProgressTruthfulnessCheck | `spoken_plan_id`, `source_progress_event_ids`, `truthfulness_level`, `check_result_ref` | caused by `SPOKEN_PLAN_EMITTED` for progress feedback | false |
| Commitment / Composer events | `PROGRESS_TRUTHFULNESS_CHECK_FAILED` | Coverage Checker / ProgressTruthfulnessCheck | `spoken_plan_id`, `source_progress_event_ids`, `failure_reasons` | caused by `SPOKEN_PLAN_EMITTED` for progress feedback | false |
| Trace / replay events | `REPLAY_STARTED` | Replay Runtime | `replay_id`, `source_trace_ref`, `replay_mode` | caused by replay request | true for replay run |
| Trace / replay events | `REPLAY_COMPLETED` | Replay Runtime | `replay_id`, `result_status`, `state_digest` | caused by replay completion | true for replay run |
| Trace / replay events | `TRACE_WRITE_DEGRADED` | Event Journal / Trace Runtime | `storage_target`, `degraded_reason` | caused by persistence/redaction/export issue | true when trace write degrades |
| Trace / replay events | `TRACE_SECRET_REDACTION_APPLIED` | Event Journal / Trace Runtime | `event_id` or `payload_ref`, `redaction_reason`, `redacted_fields` | caused by pre-write or export-time secret redaction | false |
| Trace / replay events | `TRACE_WRITE_BLOCKED_SECRET_DETECTED` | Event Journal / Trace Runtime | `source_module`, `blocked_payload_ref`, `secret_kind`, `blocking_reason` | caused by detected secret that cannot be safely redacted | false |

Replay foundation：

- InteractionState 由 Interaction events replay 重建。
- TaskFocusState 由 `ROUTER_DECISION_EMITTED` 和 `TASK_FOCUS_STATE_UPDATED` replay 重建。
- SlowTask 状态由 task events replay 重建。
- Talker playback state 由 playback events replay 重建。
- ASRFrame / SemanticFrame / ToolResult 在 replay 中默认使用记录值，不重新生成。
- stale ToolResult policy 必须基于 `task_id + plan_version + task_event_seq + caused_by_event_id` 判断。

MVP-0 至少需要记录以上 registry 中 `required_in_MVP_0=true` 或场景条件命中的事件。`required_in_MVP_0=false` 的 Duplex candidate 不是 happy-path hard requirement；只有当 hold / reject / directedness / semantic-close 场景被实现或测试时才必须记录。按族概括为：

- Session events: `SESSION_STARTED`, `ADAPTER_CAPABILITY_SNAPSHOT_RECORDED`
- Audio ingress events: `AUDIO_SPAN_STARTED`, `AUDIO_SPAN_ENDED`
- Text ingress events: `TEXT_INPUT_RECEIVED`
- Duplex events: `SPEECH_START_DETECTED`, `SPEECH_END_DETECTED`; interrupt path also records `BARGE_IN_CANDIDATE`; hold / reject / directedness paths record `DIRECTEDNESS_CANDIDATE`, `SEMANTIC_CLOSE_CANDIDATE`, or `NON_ASSISTANT_CANDIDATE` when exercised
- Interaction events: `TURN_OPENED`, `TURN_HELD`, `TURN_INGRESS_ACCEPTED`, `TURN_INGRESS_REJECTED`, `TURN_INGRESS_COMMITTED`, `INTERRUPT_CANDIDATE`, `TTS_TRUNCATE_REQUESTED`
- Router events: `ROUTER_DECISION_EMITTED`
- Mock model events: `MOCK_ASR_FRAME_EMITTED`, `MOCK_THINKER_FRAME_EMITTED`
- Playback / TTS events: `PLAYBACK_SPAN_STARTED`, `PLAYBACK_PROGRESS`, `PLAYBACK_COMMITTED`, `TTS_TRUNCATED`
- Replay marker events: `REPLAY_STARTED`, `REPLAY_COMPLETED`

MVP-1 追加：

- `SLOWTASK_CREATED`
- `SLOWTASK_STATE_CHANGED`
- `USER_PATCH_RECEIVED`
- `USER_PATCH_INTERPRETED`
- `PLAN_VERSION_ADVANCED`
- `TOOL_CALL_STARTED`
- `TOOL_RESULT_RECEIVED`
- `TOOL_RESULT_MARKED_STALE`
- `STALE_EVIDENCE_RECORDED`
- `STALE_EVIDENCE_ADOPTED` when stale evidence is reused
- `SEMANTIC_COMMITMENT_EMITTED`

MVP-1 / MVP-2 canonical addendum:

- SlowTask evidence events: `EVIDENCE_REVIEWED`, `AMBIGUITY_DETECTED`, `AMBIGUITY_RESOLVED`, `CLARIFICATION_REQUESTED`, `ARGUMENTS_RESOLVED`, `ARGUMENT_RESOLUTION_PROVENANCE`, `INSUFFICIENT_EVIDENCE_FOR_ACTION`
- SlowTask lifecycle/progress events: `TASK_REPLANNED`, `PLANNING_STARTED`, `PLANNING_RESTARTED`, `WAITING_FOR_SLOT`, `WAITING_FOR_TOOL`, `WAITING_FOR_USER_CONFIRMATION`, `FINALIZING`, `SLOWTASK_DEGRADED`, `SLOWTASK_FAILED`
- Confirmation/cancellation events: `CONFIRMATION_REQUIRED`, `USER_CONFIRMATION_RECEIVED`, `CONFIRMATION_ACCEPTED`, `CONFIRMATION_REJECTED`, `SLOWTASK_CANCEL_REQUESTED`, `SLOWTASK_CANCELLED`, `TOOL_EXECUTION_CANCEL_REQUESTED`
- Progressive tool events: `TOOL_MANIFEST_LOADED`, `TOOL_ARGUMENTS_PARTIAL`, `TOOL_ARGUMENTS_READY`, `TOOL_PREVIEW_AVAILABLE`, `TOOL_EXECUTION_AUTHORIZED`, `TOOL_EXECUTION_STARTED`, `TOOL_PROGRESS_UPDATED`, `TOOL_UI_STATE_PATCHED`, `TOOL_EXECUTION_FAILED`, `TOOL_CALL_RETRYING`, `TOOL_EXECUTION_CANCELLED`, `TOOL_EXECUTION_BLOCKED_INSUFFICIENT_ARGUMENTS`
- Composer/progress safety events: `COMMITMENT_COVERAGE_CHECK_PASSED`, `COMMITMENT_COVERAGE_CHECK_FAILED`, `PROGRESS_TRUTHFULNESS_CHECK_PASSED`, `PROGRESS_TRUTHFULNESS_CHECK_FAILED`
- Adapter events: `ADAPTER_HEALTHCHECK_FAILED`, `ADAPTER_REQUEST_RETRYING`, `ADAPTER_REQUEST_FAILED`, `ADAPTER_OUTPUT_VALIDATION_FAILED`, `ADAPTER_OUTPUT_DEGRADED` when those paths occur
- Trace safety events: `TRACE_SECRET_REDACTION_APPLIED`, `TRACE_WRITE_BLOCKED_SECRET_DETECTED`

MVP-3 canonical addendum:

- ASR adapter contract event: `ASR_TRANSCRIPT_OUTPUT_EMITTED` for final transcript or equivalent text projection refs. Replay uses recorded refs and metadata only; it does not require raw audio or rerun the ASR provider.
- Thinker adapter contract event: `THINKER_SEMANTIC_FRAME_OUTPUT_EMITTED` for normalized SemanticFrame-compatible refs and metadata. Replay uses recorded refs and metadata only; it does not rerun the Thinker provider, and missing semantic close, assistant-directedness, emotion, or audio caption must be explicit degraded metadata rather than default values.
- Slow LLM adapter contract event: `SLOW_LLM_STRUCTURED_OUTPUT_EMITTED` for validated normalized SlowTask-compatible refs and metadata. Replay uses recorded refs only; it does not rerun the Slow LLM provider, and invalid structured output must remain `ADAPTER_OUTPUT_VALIDATION_FAILED` evidence rather than downstream SlowTask input.
- TTS adapter contract event: `TTS_SYNTHESIS_OUTPUT_EMITTED` for normalized playback-compatible audio refs and metadata. Replay uses recorded refs only; it does not rerun the TTS provider, does not require raw audio, and missing truncate capability must be explicit degraded/blocking metadata rather than a silent barge-in target validation pass. MVP-3 approved playback must bind to exactly one prior TTS output by explicit event id or unique safe ref match.

后续 ADR 若新增 MVP-relevant event，必须同时更新本 registry 或明确声明该名称只是 payload enum / state value，而不是 journal event name。

## Alternatives Considered

1. 每个模块独立日志，后处理时合并。
   优点是实现简单；缺点是跨模块因果和音频/playback 对齐困难，replay 不可靠。

2. 全局同步事件总线，所有模块必须经由总线通信。
   优点是顺序统一；缺点是会成为实时链路阻塞点，不适合作为 MVP-0 live loop 基础。

3. 只记录 debug log，不建立结构化 event journal。
   优点是成本低；缺点是无法稳定做 replay、SLO 统计、plan_version consistency 和 stale result 判断。

4. 只在 SlowTask 层做事件溯源。
   优点是慢任务一致性较好；缺点是无法解释 barge-in、turn boundary、TTS truncate 等 live 语音问题。

## Consequences

正向结果：

- replay 可以重建关键状态，而不是依赖自然语言日志。
- interrupt、truncate、playback commitment 有可审计依据。
- UserPatch、ToolResult、SemanticCommitment 可以绑定 plan_version。
- SLO 可以基于 event timestamps 计算。
- mock phase 和真实 adapter phase 使用同一套观测基础。

代价：

- 需要尽早定义 event envelope 和事件命名规范。
- 每个模块都要把关键状态迁移显式落 event。
- 异步持久化意味着 crash 时可能丢失尾部事件，需要定义 flush 策略。
- replay 只能保证重建记录过的事实，不能自动证明模型当时“为什么”这么输出，除非 prompt/input/output 也按隐私策略记录。

## Impacted Modules

- Session Runtime
- Event Journal
- Access Layer
- Duplex / Realtime Conversation Gate
- Interaction / Turn Controller
- ASR Adapter
- Thinker Adapter
- Router
- TaskFocusState
- SlowTask
- Tool Executor
- Talker
- Composer
- Trace / Replay
- Evaluation Harness
- Privacy / Redaction Policy

## Validation Method

MVP-0 必须验证：

1. 同一 session 内所有事件都有严格递增 `event_seq` 和有效 `event_schema_version`。
2. audio path 的 `TURN_INGRESS_COMMITTED` 能通过 `caused_by_event_id` 追溯到 Duplex / audio events；text path 能追溯到 `TEXT_INPUT_RECEIVED`。
3. `TTS_TRUNCATE_REQUESTED` 必须包含 `playback_span_id` 和 `cutoff_playback_offset_ms`。
4. replay 后的 InteractionState 与原运行状态一致。
5. replay 能计算 speech_start latency、barge-in to truncate latency、first acknowledgement latency。
6. 没有 `TURN_INGRESS_COMMITTED` 的 audio span 不会生成 ASRFrame / SemanticFrame。
7. journal 异步持久化失败时，runtime 能暴露 error event 或 degraded trace 状态。
8. 文本输入必须先记录 `TEXT_INPUT_RECEIVED`，再由 Interaction Controller 产生 `TURN_OPENED`、`TURN_INGRESS_ACCEPTED` 和 `TURN_INGRESS_COMMITTED`；不得由 Access Layer 直接进入 Router。
9. `PLAYBACK_COMMITTED` 只能作为 playback delivery marker，不得作为用户理解或确认的语义证据。
10. session startup 必须记录 `ADAPTER_CAPABILITY_SNAPSHOT_RECORDED`，使 mock / real / fallback / degraded 能力状态可 replay。

MVP-1 必须验证：

1. UserPatch、ToolCall、ToolResult、SemanticCommitment 都绑定 `task_id` 和 `plan_version`。
2. 旧 `plan_version` 的 ToolResult replay 后进入 stale_evidence，不推进 current plan。
3. SlowTask 状态 replay 后与运行时最终状态一致。
4. `supersedes_event_id` 能解释 plan_version advance 或旧结果废弃关系；若旧结果被复用，`STALE_EVIDENCE_ADOPTED` 能解释 adopt / rebase 范围。

MVP-2 必须验证：

1. ADR-005 / ADR-008 / ADR-013 引用的 progressive tool、evidence review、progress feedback event 都使用本 registry 中的 canonical names。
2. `TOOL_EXECUTION_STARTED` 可追溯到 current-plan arguments / authorization events。
3. `TOOL_UI_STATE_PATCHED` replay 后能重建 demo frontend state。
4. `PROGRESS_TRUTHFULNESS_CHECK_PASSED` / `FAILED` 和 `COMMITMENT_COVERAGE_CHECK_PASSED` / `FAILED` 在 replay 中可区分，并能解释 Talker playback 为什么被允许或阻止。
5. trace safety events 不包含被 redacted / blocked 的原始 secret 值。

## Open Questions

- event journal 文件格式后续采用 JSONL、SQLite，还是先内存结构加导出？
- crash 前未 flush 的尾部事件是否接受丢失，还是关键事件必须同步 flush？
- `event_seq` 是否只在 session 内递增，还是 conversation 内跨 session 也需要单调？
- replay 是否需要支持 deterministic mode 和 re-eval mode 两种？
- MVP-0 是否记录 mock model 的完整 input/output，还是只记录摘要和 event metadata？
