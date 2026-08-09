from __future__ import annotations

"""Deterministic fake-provider profiles, never a generic runtime default."""

from dataclasses import dataclass
import re
from typing import Any

from voice_agent.slowtask.slot_ledger import SlotState, SlotUpdate


@dataclass(frozen=True)
class TaskProfile:
    profile_id: str
    match_terms: tuple[str, ...]
    model_payload: dict[str, Any]


class TaskProfileRegistry:
    def __init__(self, profiles: tuple[TaskProfile, ...]) -> None:
        self._profiles = {profile.profile_id: profile for profile in profiles}

    def get(self, profile_id: str) -> TaskProfile:
        return self._profiles[profile_id]

    def select_fake_fixture(self, intent: str) -> TaskProfile | None:
        scored = [
            (sum(term.lower() in intent.lower() for term in profile.match_terms), profile.profile_id, profile)
            for profile in self._profiles.values()
            if profile.profile_id != "generic_bootstrap"
        ]
        matches = [item for item in scored if item[0] > 0]
        return max(matches, default=(0, "", None))[2]

    def generic_bootstrap(self) -> TaskProfile:
        return self.get("generic_bootstrap")

    def profiles(self) -> tuple[TaskProfile, ...]:
        return tuple(self._profiles[name] for name in sorted(self._profiles))


def builtin_task_profile_registry() -> TaskProfileRegistry:
    return TaskProfileRegistry(
        (
            TaskProfile(
                "customer_reception",
                ("客户", "接待", "午餐", "晚餐", "宴请"),
                _model(
                    "customer_reception",
                    "客户接待规划",
                    ("reception", "meal"),
                    (
                        _requirement("time_window", "接待时间", "明确接待日期和时段", "time_window", "USER", "search", "reception_basics", 10),
                        _requirement("location_anchor", "位置范围", "接待或活动的位置锚点", "location", "USER", "search", "reception_basics", 20),
                        _requirement("party_size", "参与人数", "需要接待的大概人数", "integer", "USER", "search", "reception_basics", 30, validation="positive_integer"),
                        _requirement("budget", "预算范围", "用餐或活动预算", "number", "USER", "plan", "constraints", 40, validation="bounded_number", activation_component="meal"),
                        _requirement("dietary_constraints", "饮食限制", "忌口、过敏或明确无忌口", "string_list", "USER", "plan", "constraints", 50, validation="explicit_empty_allowed", allow_empty=True, activation_component="meal"),
                        _requirement("cuisine_preference", "餐饮偏好", "菜系或餐型偏好", "string", "OPTIONAL", None, "preferences", 60),
                        _requirement("venue_options", "地点候选", "由只读地点检索获得的候选", "string_list", "TOOL", "plan", "tool_evidence", 70, tool=("webSearch", "query")),
                    ),
                    ("覆盖时间、地点、人数和关键约束", "不得把地点候选说成已预订"),
                ),
            ),
            TaskProfile(
                "research_report",
                ("调研报告", "研究报告", "proactive agent", "文献综述"),
                _model(
                    "research_report",
                    "调研报告规划",
                    ("research", "writing"),
                    (
                        _requirement("research_scope", "研究范围", "报告需要回答的核心主题和边界", "string", "USER", "plan", "scope", 10),
                        _requirement("target_audience", "目标读者", "报告面向的读者及其背景", "string", "USER", "plan", "audience", 20),
                        _requirement("output_format", "输出形式", "篇幅、结构或交付格式", "string", "USER", "plan", "deliverable", 30),
                        _requirement("deadline", "截止时间", "需要交付的时间", "date", "OPTIONAL", None, "schedule", 40),
                        _requirement("source_coverage", "资料覆盖", "通过研究工具获得可信资料线索", "string_list", "TOOL", "plan", "tool_evidence", 50, tool=("webSearch", "query")),
                    ),
                    ("明确研究问题和读者", "给出可执行的资料收集与写作步骤", "区分事实来源和待验证判断"),
                ),
            ),
            TaskProfile(
                "code_refactor",
                ("代码重构", "重构计划", "refactor", "技术债"),
                _model(
                    "code_refactor",
                    "代码重构计划",
                    ("software", "refactoring"),
                    (
                        _requirement("refactor_scope", "重构范围", "需要调整的仓库、模块或边界", "string", "USER", "plan", "scope", 10),
                        _requirement("refactor_objectives", "重构目标", "希望改善的质量属性或问题", "string_list", "USER", "plan", "goals", 20),
                        _requirement("compatibility_constraints", "兼容约束", "必须保持的 API、行为和迁移限制", "string_list", "USER", "plan", "constraints", 30, validation="explicit_empty_allowed", allow_empty=True),
                        _requirement("acceptance_criteria", "验收标准", "可验证的完成条件", "string_list", "USER", "plan", "acceptance", 40),
                    ),
                    ("覆盖目标模块和依赖边界", "包含兼容与回滚策略", "每个阶段有可执行的验收方法"),
                ),
            ),
            TaskProfile(
                "team_event",
                ("团建", "团队活动", "team building"),
                _model(
                    "team_event",
                    "团队团建规划",
                    ("event", "team_activity"),
                    (
                        _requirement("party_size", "参与人数", "预计参加团建的人数", "integer", "USER", "search", "participants", 10, validation="positive_integer"),
                        _requirement("event_date", "活动日期", "团建日期或可接受时间窗口", "time_window", "USER", "search", "schedule", 20),
                        _requirement("city", "活动城市", "活动所在城市或区域", "location", "USER", "search", "logistics", 30),
                        _requirement("budget", "预算范围", "人均或总预算", "number", "USER", "plan", "constraints", 40, validation="bounded_number"),
                        _requirement("activity_preferences", "活动偏好", "希望或不希望出现的活动类型", "string_list", "USER", "plan", "preferences", 50),
                        _requirement("venue_options", "场地候选", "由只读地点检索获得的活动场地", "string_list", "TOOL", "plan", "tool_evidence", 60, tool=("webSearch", "query")),
                    ),
                    ("人数、日期、城市和预算可执行", "活动偏好有覆盖", "候选场地保持未预订语义"),
                ),
            ),
            TaskProfile(
                "generic_bootstrap",
                (),
                _model(
                    "generic_bootstrap",
                    "待澄清的通用规划任务",
                    ("generic_planning",),
                    (
                        _requirement("desired_deliverable", "最终产出", "用户最终希望得到的具体产出", "string", "USER", "plan", "bootstrap", 10),
                        _requirement("key_constraint", "最重要约束", "当前最重要且不能违反的约束", "string", "USER", "plan", "bootstrap", 20),
                    ),
                    ("明确最终产出", "明确首要约束"),
                ),
            ),
        )
    )


def extract_profile_updates(
    *, profile_id: str, text: str, evidence_ref: str, plan_version: int, source: str
) -> tuple[SlotUpdate, ...]:
    extractors = {
        "customer_reception": _extract_customer_reception,
        "research_report": _extract_research_report,
        "code_refactor": _extract_code_refactor,
        "team_event": _extract_team_event,
        "generic_bootstrap": _extract_generic_bootstrap,
    }
    extractor = extractors.get(profile_id, _extract_generic_bootstrap)
    values = extractor(text)
    return tuple(
        SlotUpdate(
            name=name,
            normalized_value=value[0],
            raw_evidence=" ".join(text.split())[:320],
            state=value[1],
            evidence_ref=evidence_ref,
            source=source,
            plan_version=plan_version,
        )
        for name, value in values.items()
    )


def _extract_customer_reception(text: str) -> dict[str, tuple[Any, SlotState]]:
    values: dict[str, tuple[Any, SlotState]] = {}
    compact = re.sub(r"\s+", "", text)
    location = re.search(r"(?:在|地点(?:是|在)?|位置(?:是|在)?)([^，。；;]{2,40})", text)
    location_candidate = location.group(1).strip() if location else ""
    if location_candidate and not _is_temporal_phrase(location_candidate):
        values["location_anchor"] = (location_candidate, SlotState.RESOLVED)
    elif any(term in text for term in ("中关村", "北京", "公司", "酒店", "机场", "附近")):
        values["location_anchor"] = (" ".join(text.split())[:120], SlotState.RESOLVED)
    time = re.search(r"(\d{1,2})[点:：](半|\d{1,2})?", compact)
    if time:
        values["time_window"] = (f"{int(time.group(1)):02d}:{'30' if time.group(2) == '半' else time.group(2) or '00'}", SlotState.RESOLVED)
    elif any(term in text for term in ("下周吧", "下周左右", "下周都行")):
        values["time_window"] = ("下周（日期和时段不明确）", SlotState.AMBIGUOUS)
    elif any(term in text for term in ("今天", "明天", "后天", "上午", "下午", "中午", "晚上", "午饭", "午餐", "晚饭", "晚餐", "周一", "周二", "周三", "周四", "周五", "周六", "周日")):
        values["time_window"] = (" ".join(text.split())[:120], SlotState.RESOLVED)
    party = re.search(r"(\d+|[一二三四五六七八九十两]+)\s*(?:个|位)?人", text)
    if party:
        values["party_size"] = (_chinese_or_digit_number(party.group(1)), SlotState.RESOLVED)
    budget = re.search(r"(?:人均|总预算)?\s*(\d+)\s*(?:元|块|以内|以下)", text)
    if budget:
        values["budget"] = (int(budget.group(1)), SlotState.RESOLVED)
    if any(term in text for term in ("无忌口", "没有忌口", "没忌口", "没有饮食限制")):
        values["dietary_constraints"] = ([], SlotState.RESOLVED)
    elif "不吃辣" in text:
        values["dietary_constraints"] = (["不吃辣"], SlotState.RESOLVED)
    if "云南菜" in text or "滇菜" in text:
        values["cuisine_preference"] = ("云南菜", SlotState.RESOLVED)
    return values


def _is_temporal_phrase(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    temporal_markers = (
        "今天", "明天", "后天", "本周", "下周", "周一", "周二", "周三", "周四",
        "周五", "周六", "周日", "上午", "中午", "下午", "晚上", "早上",
    )
    return any(compact.startswith(marker) for marker in temporal_markers)


def _extract_research_report(text: str) -> dict[str, tuple[Any, SlotState]]:
    values: dict[str, tuple[Any, SlotState]] = {}
    topic = re.search(r"关于\s*([^，。]{2,80})\s*的?(?:调研|研究|报告)", text, re.IGNORECASE)
    if topic:
        values["research_scope"] = (topic.group(1).strip(), SlotState.RESOLVED)
    elif any(term in text.lower() for term in ("proactive agent", "调研报告", "研究报告")):
        values["research_scope"] = (" ".join(text.split())[:160], SlotState.RESOLVED)
    if "面向" in text:
        values["target_audience"] = (text.split("面向", 1)[1][:80], SlotState.RESOLVED)
    if any(term in text for term in ("大纲", "报告", "文档", "PPT")):
        values["output_format"] = ("结构化调研报告" if "报告" in text else "规划大纲", SlotState.RESOLVED)
    return values


def _extract_code_refactor(text: str) -> dict[str, tuple[Any, SlotState]]:
    values: dict[str, tuple[Any, SlotState]] = {}
    repo = re.search(r"给\s*([A-Za-z0-9_.-]+)\s*(?:设计|做|制定)", text)
    if repo:
        values["refactor_scope"] = (repo.group(1), SlotState.RESOLVED)
    elif "voice-agent" in text:
        values["refactor_scope"] = ("voice-agent", SlotState.RESOLVED)
    if any(term in text for term in ("目标是", "为了", "改善")):
        values["refactor_objectives"] = ([" ".join(text.split())[:160]], SlotState.RESOLVED)
    if "兼容" in text:
        values["compatibility_constraints"] = (["保持现有兼容行为"], SlotState.RESOLVED)
    if any(term in text for term in ("验收", "测试通过", "标准")):
        values["acceptance_criteria"] = ([" ".join(text.split())[:160]], SlotState.RESOLVED)
    return values


def _extract_team_event(text: str) -> dict[str, tuple[Any, SlotState]]:
    values = _extract_customer_reception(text)
    mapped: dict[str, tuple[Any, SlotState]] = {}
    if "party_size" in values:
        mapped["party_size"] = values["party_size"]
    if "time_window" in values:
        mapped["event_date"] = values["time_window"]
    if "location_anchor" in values:
        mapped["city"] = values["location_anchor"]
    if "budget" in values:
        mapped["budget"] = values["budget"]
    preferences = [term for term in ("户外", "室内", "运动", "桌游", "聚餐", "拓展") if term in text]
    if preferences:
        mapped["activity_preferences"] = (preferences, SlotState.RESOLVED)
    return mapped


def _extract_generic_bootstrap(text: str) -> dict[str, tuple[Any, SlotState]]:
    values: dict[str, tuple[Any, SlotState]] = {}
    if any(term in text for term in ("产出", "得到", "交付")):
        values["desired_deliverable"] = (" ".join(text.split())[:160], SlotState.RESOLVED)
    if any(term in text for term in ("必须", "约束", "不能")):
        values["key_constraint"] = (" ".join(text.split())[:160], SlotState.RESOLVED)
    return values


def _model(
    task_kind: str,
    summary: str,
    components: tuple[str, ...],
    requirements: tuple[dict[str, Any], ...],
    criteria: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "model_id": f"model_{task_kind}",
        "model_version": 1,
        "task_kind": task_kind,
        "task_summary": summary,
        "task_components": list(components),
        "requirements": list(requirements),
        "success_criteria": list(criteria),
        "applicable_stages": ["search", "plan", "commitment"],
        "source_proposal_ref": "proposal://fake/task_modeler",
        "accepted_context_hash": "sha256:pending",
    }


def _requirement(
    requirement_id: str,
    label: str,
    description: str,
    value_type: str,
    source_route: str,
    required_at: str | None,
    question_group: str,
    priority: int,
    *,
    validation: str = "non_empty",
    allow_empty: bool = False,
    tool: tuple[str, str] | None = None,
    activation_component: str | None = None,
) -> dict[str, Any]:
    rule: dict[str, Any] = {"operator": validation}
    if validation == "bounded_number":
        rule.update({"minimum": 0, "maximum": 1_000_000})
    return {
        "requirement_id": requirement_id,
        "label": label,
        "description": description,
        "value_type": value_type,
        "source_route": source_route,
        "required_at": required_at,
        "importance": "high" if required_at else "normal",
        "activation": {"operator": "always"} if activation_component is None else {"operator": "component_present", "component": activation_component},
        "validation_rule": rule,
        "question_group": question_group,
        "priority": priority,
        "tool_bindings": [] if tool is None else [{"tool_name": tool[0], "argument_name": tool[1]}],
        "sensitive": False,
        "allow_explicit_empty": allow_empty,
    }


def _chinese_or_digit_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(value, 0)


__all__ = ["TaskProfile", "TaskProfileRegistry", "builtin_task_profile_registry", "extract_profile_updates"]
