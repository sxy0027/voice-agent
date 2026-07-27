from __future__ import annotations

"""Bounded, read-only public place-search adapter for the Workbench.

The adapter intentionally returns a *small evidence projection*, rather than
raw pages.  It is outside SlowTask and Tool Executor: Tool Executor still owns
the journalled lifecycle, and SlowTask/Composer decide how cautiously the
evidence is expressed.  No request made here can make a reservation or change
an external system.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
import json
import re
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


# Brave/Sogou server-rendered search payloads can exceed 500KB.  This remains
# a strict bounded projection while avoiding a false “no result” degradation.
MAX_RESPONSE_BYTES = 700_000
MAX_RESULTS = 4
REQUEST_TIMEOUT_SECONDS = 8
_UNSAFE_MARKERS = ("bearer ", "api_key=", "authorization=", "cookie:", "file://", "/users/")
_USER_AGENT = "voice-agent-workbench/1.1 (read-only demo; contact: local-workbench)"


@dataclass(frozen=True)
class PlaceSearchEvidence:
    query: str
    results: tuple[Mapping[str, str], ...]
    provider: str
    degraded_reason: str | None = None


class WorkbenchPlaceSearchAdapter:
    """Use independent public, read-only sources with clear degradation.

    DuckDuckGo's HTML endpoint frequently returns a challenge page (HTTP 202)
    in the local demo.  Treating that as ``no restaurants exist`` was the
    immediate cause of the vague answer seen in the Workbench.  OpenStreetMap
    Nominatim contributes map POIs; Brave's public result page contributes
    attributed web candidates.  Either source is enough to form a bounded
    candidate list, and every source URL remains visible to the user.
    """

    def search(self, *, query: str) -> PlaceSearchEvidence:
        safe_query = _safe_text(query, maximum=240)
        if not safe_query:
            return PlaceSearchEvidence(query="", results=(), provider="unavailable", degraded_reason="empty_query")

        map_results, map_status = _search_nominatim(safe_query)
        web_results, web_status = _search_brave(safe_query)
        # Brave occasionally rate-limits a local development IP.  Sogou is a
        # second, independently operated public source; use it only when the
        # first two sources cannot yield a usable shortlist.
        sogou_results: list[dict[str, str]] = []
        sogou_status = "not_needed"
        if len(_dedupe_results((*map_results, *web_results))) < 2:
            sogou_results, sogou_status = _search_sogou(safe_query)
        results = _dedupe_results((*map_results, *web_results, *sogou_results))[:MAX_RESULTS]
        provider = "nominatim_osm+brave_html+sogou_html"
        if results:
            return PlaceSearchEvidence(query=safe_query, results=tuple(results), provider=provider)

        statuses = tuple(status for status in (map_status, web_status, sogou_status) if status != "not_needed")
        if all(status == "unavailable" for status in statuses):
            reason = "external_read_unavailable"
        elif any(status == "response_too_large" for status in statuses):
            reason = "external_read_response_too_large"
        else:
            reason = "no_relevant_place_evidence"
        return PlaceSearchEvidence(query=safe_query, results=(), provider=provider, degraded_reason=reason)


def _search_nominatim(query: str) -> tuple[list[dict[str, str]], str]:
    """Return mappable POIs, never raw provider data."""

    document, status = _read_public_url(
        "https://nominatim.openstreetmap.org/search?format=jsonv2&addressdetails=1&limit=4&q="
        + quote_plus(query)
    )
    if document is None:
        return [], status
    try:
        payload = json.loads(document.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return [], "invalid_response"
    if not isinstance(payload, list):
        return [], "invalid_response"
    results: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        title = _safe_text(str(item.get("name") or item.get("display_name") or ""), maximum=180)
        osm_type = _safe_text(str(item.get("osm_type", "")), maximum=20)
        osm_id = _safe_text(str(item.get("osm_id", "")), maximum=32)
        display_name = _safe_text(str(item.get("display_name", "")), maximum=260)
        if not title or osm_type not in {"node", "way", "relation"} or not osm_id:
            continue
        results.append(
            {
                "source_title": title,
                "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                "snippet_or_summary": display_name or "来自 OpenStreetMap 的地点记录；营业、菜单和余位需向门店复核。",
                "source_provider": "OpenStreetMap Nominatim",
            }
        )
    return results, "ok"


def _search_brave(query: str) -> tuple[list[dict[str, str]], str]:
    """Return short, attributed web snippets from Brave's public result page."""

    document, status = _read_public_url("https://search.brave.com/search?q=" + quote_plus(query) + "&source=web")
    if document is None:
        return [], status
    text = document.decode("utf-8", errors="replace")
    # The SSR payload stores result data as JavaScript strings.  This pattern
    # is deliberately bounded and extracts only title, direct URL and snippet.
    pattern = re.compile(
        r'title:"(?P<title>(?:\\.|[^"\\])*)"\s*,\s*'
        r'url:"(?P<url>(?:\\.|[^"\\])*)".*?'
        r'description:"(?P<snippet>(?:\\.|[^"\\])*)"',
        re.IGNORECASE | re.DOTALL,
    )
    results: list[dict[str, str]] = []
    for match in pattern.finditer(text):
        title = _strip_html(_decode_js_string(match.group("title")))
        url = _safe_url(_decode_js_string(match.group("url")))
        snippet = _strip_html(_decode_js_string(match.group("snippet")))
        if not title or not url:
            continue
        results.append(
            {
                "source_title": title,
                "source_url": url,
                "snippet_or_summary": snippet,
                "source_provider": "Brave Search",
            }
        )
        if len(results) >= MAX_RESULTS:
            break
    return results, "ok"


def _search_sogou(query: str) -> tuple[list[dict[str, str]], str]:
    """Fallback parser for short, visible result cards from Sogou.

    Individual Sogou result URLs can contain transient signed redirect tokens.
    They are deliberately not persisted.  Each candidate instead cites the
    bounded public search page for this exact query, which is stable enough for
    the user to verify while avoiding token-like redirect URLs in evidence.
    """

    search_url = "https://www.sogou.com/web?query=" + quote_plus(query)
    document, status = _read_public_url(search_url)
    if document is None:
        return [], status
    text = document.decode("utf-8", errors="replace")
    results: list[dict[str, str]] = []
    for heading in re.finditer(r"<h3\b[^>]*>(?P<title>.*?)</h3>", text, re.IGNORECASE | re.DOTALL):
        title = _strip_html(heading.group("title"))
        if not _looks_like_place_candidate(title):
            continue
        following = text[heading.end() : heading.end() + 4_000]
        summary_match = re.search(
            r"<(?:div|p)\b[^>]*(?:summary|fz-mid|space-txt)[^>]*>(?P<summary>.*?)</(?:div|p)>",
            following,
            re.IGNORECASE | re.DOTALL,
        )
        summary = _strip_html(summary_match.group("summary")) if summary_match else "公开搜索结果显示的地点候选；营业、地址、菜单和余位需向门店复核。"
        results.append(
            {
                "source_title": title,
                "source_url": search_url,
                "snippet_or_summary": summary,
                "source_provider": "Sogou public search",
            }
        )
        if len(results) >= MAX_RESULTS:
            break
    return results, "ok"


def _read_public_url(url: str) -> tuple[bytes | None, str]:
    request = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/json"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 -- fixed HTTPS providers
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception:
        return None, "unavailable"
    if len(raw) > MAX_RESPONSE_BYTES:
        return None, "response_too_large"
    return raw, "ok"


def _decode_js_string(value: str) -> str:
    try:
        return str(json.loads(f'"{value}"'))
    except json.JSONDecodeError:
        return value


def _dedupe_results(items: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for item in items:
        key = (item["source_title"].casefold(), item["source_url"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _looks_like_place_candidate(title: str) -> bool:
    normalized = title.casefold()
    if not any(marker in normalized for marker in ("云南", "滇菜", "米线")):
        return False
    return not any(
        marker in normalized
        for marker in ("搜索", "美食攻略", "大盘点", "排行榜", "推荐!", "推荐！", "图片", "视频", "馆电话", "馆地址")
    )


def _strip_html(value: str) -> str:
    return _safe_text(unescape(re.sub(r"<[^>]+>", " ", value)), maximum=260)


def _safe_url(value: str) -> str:
    normalized = _safe_text(value, maximum=500)
    if not normalized.startswith(("https://", "http://")):
        return ""
    return "" if any(marker in normalized.lower() for marker in _UNSAFE_MARKERS) else normalized


def _safe_text(value: str, *, maximum: int) -> str:
    normalized = " ".join(str(value).split())
    if any(marker in normalized.lower() for marker in _UNSAFE_MARKERS):
        return ""
    return normalized[:maximum]


__all__ = ["PlaceSearchEvidence", "WorkbenchPlaceSearchAdapter"]
