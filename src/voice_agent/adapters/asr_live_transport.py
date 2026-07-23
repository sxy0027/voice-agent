from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import json
import urllib.error
import urllib.request
from typing import Any

from voice_agent.adapters.capabilities import CREDENTIAL_LIKE_REF_PATTERN


ASR_LIVE_SELECTED_MODEL_ALIAS = "qwen3-asr-flash"
ASR_LIVE_DASHSCOPE_OPENAI_COMPATIBLE_CHAT_COMPLETIONS_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
ASR_LIVE_DASHSCOPE_PROVIDER_URL_REF = (
    "provider-url://dashscope/qwen-asr/openai-compatible-chat-completions"
)


class DashScopeAsrLiveTransportError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        failure_reasons: Sequence[str] | None = None,
        retryable: bool = False,
        timeout: bool = False,
    ) -> None:
        super().__init__(message)
        self.failure_reasons = list(_safe_failure_reasons(failure_reasons or (message,)))
        self.retryable = retryable
        self.timeout = timeout


@dataclass(frozen=True)
class AsrLiveCredentialHandle:
    credential_ref: str

    def __post_init__(self) -> None:
        _require_safe_ref(self.credential_ref, "credential_ref")

    def __repr__(self) -> str:
        return (
            "AsrLiveCredentialHandle("
            f"credential_ref={self.credential_ref!r}, credential_present=True, "
            "secret_materialized=False)"
        )

    def __str__(self) -> str:
        raise DashScopeAsrLiveTransportError(
            "ASR live credential handle is opaque and not string serializable"
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "credential_ref": self.credential_ref,
            "credential_present": True,
            "secret_materialized": False,
        }


@dataclass(frozen=True)
class AsrLiveProviderCallMetadata:
    adapter_request_id: str
    provider_url_ref: str
    model_alias: str
    transcript_present: bool
    asr_frame_ref: str
    text_ref: str
    response_text_size_bucket: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "adapter_request_id": self.adapter_request_id,
            "provider_transport": "direct_http",
            "provider_url_ref": self.provider_url_ref,
            "model_alias": self.model_alias,
            "success": True,
            "transcript_present": self.transcript_present,
            "asr_frame_ref": self.asr_frame_ref,
            "text_ref": self.text_ref,
            "response_text_size_bucket": self.response_text_size_bucket,
            "raw_audio_included": False,
            "raw_transcript_included": False,
            "raw_provider_request_included": False,
            "raw_provider_response_included": False,
            "headers_included": False,
            "authorization_header_included": False,
            "secret_included": False,
        }


class DashScopeAsrLiveDirectHTTPTransport:
    """Adapter-internal direct HTTP transport; tests inject an opener."""

    def __init__(
        self,
        *,
        provider_url: str = ASR_LIVE_DASHSCOPE_OPENAI_COMPATIBLE_CHAT_COMPLETIONS_URL,
        opener: object | None = None,
    ) -> None:
        if not isinstance(provider_url, str) or provider_url == "":
            raise DashScopeAsrLiveTransportError("provider_url must be a non-empty string")
        self._provider_url = provider_url
        self._opener = opener if opener is not None else urllib.request.build_opener()

    def transcribe(
        self,
        *,
        audio_payload: bytes,
        audio_mime_type: str,
        credential_handle: AsrLiveCredentialHandle,
        credential_value: str,
        adapter_request_id: str,
        timeout_ms: int,
        model_alias: str,
    ) -> AsrLiveProviderCallMetadata:
        validate_asr_live_credential_handle(credential_handle)
        _require_present_credential_value(credential_value)
        adapter_request_id = _require_safe_ref(adapter_request_id, "adapter_request_id")
        _require_positive_int(timeout_ms, "timeout_ms")
        model_alias = _require_safe_ref(model_alias, "model_alias")
        _require_audio_payload(audio_payload)
        audio_format = _audio_format_from_mime_type(audio_mime_type)

        request_body = _build_openai_compatible_asr_request_body(
            audio_payload=audio_payload,
            audio_mime_type=audio_mime_type,
            audio_format=audio_format,
            model_alias=model_alias,
        )
        _store_asr_model_io_request(
            adapter_request_id=adapter_request_id,
            model_alias=model_alias,
            request_body=request_body,
        )
        request = urllib.request.Request(
            self._provider_url,
            data=json.dumps(request_body, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            ),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {credential_value}",
            },
            method="POST",
        )

        try:
            with self._opener.open(request, timeout=timeout_ms / 1000) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise DashScopeAsrLiveTransportError(
                "provider timeout",
                failure_reasons=("provider_timeout",),
                retryable=True,
                timeout=True,
            ) from exc
        except urllib.error.HTTPError as exc:
            raise DashScopeAsrLiveTransportError(
                "provider request failed",
                failure_reasons=(
                    "provider_request_failed",
                    _http_status_class_category(exc.code),
                ),
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise DashScopeAsrLiveTransportError(
                    "provider timeout",
                    failure_reasons=("provider_timeout",),
                    retryable=True,
                    timeout=True,
                ) from exc
            raise DashScopeAsrLiveTransportError(
                "provider request failed",
                failure_reasons=("provider_request_failed",),
            ) from exc
        except json.JSONDecodeError as exc:
            raise DashScopeAsrLiveTransportError(
                "provider response parse failed",
                failure_reasons=("provider_response_parse_failed",),
            ) from exc

        response_text = _extract_response_text(response_payload)
        _store_asr_model_io_response(
            adapter_request_id=adapter_request_id,
            provider_text=response_text,
        )
        asr_frame_ref, text_ref = _store_local_transcript_projection(
            adapter_request_id=adapter_request_id,
            transcript_text=response_text,
        )
        return AsrLiveProviderCallMetadata(
            adapter_request_id=adapter_request_id,
            provider_url_ref=ASR_LIVE_DASHSCOPE_PROVIDER_URL_REF,
            model_alias=model_alias,
            transcript_present=bool(response_text.strip()),
            asr_frame_ref=asr_frame_ref,
            text_ref=text_ref,
            response_text_size_bucket=_response_text_size_bucket(response_text),
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "provider_transport": "direct_http",
            "provider_url_ref": ASR_LIVE_DASHSCOPE_PROVIDER_URL_REF,
            "raw_provider_request_included": False,
            "raw_provider_response_included": False,
            "headers_included": False,
            "authorization_header_included": False,
            "secret_materialized": False,
        }


def validate_asr_live_credential_handle(
    credential_handle: AsrLiveCredentialHandle,
) -> AsrLiveCredentialHandle:
    if not isinstance(credential_handle, AsrLiveCredentialHandle):
        raise DashScopeAsrLiveTransportError("credential_handle must be ASR live handle")
    _require_safe_ref(credential_handle.credential_ref, "credential_ref")
    return credential_handle


_LOCAL_TRANSCRIPT_TEXT_BY_REF: dict[str, str] = {}
_LOCAL_ASR_MODEL_IO_BY_ADAPTER_REQUEST_ID: dict[str, dict[str, Any]] = {}


def resolve_asr_live_transcript_text_ref(text_ref: str) -> str | None:
    """Resolve process-local live ASR transcript text by safe ref.

    The raw transcript stays in memory only; adapter events and summaries carry the
    ref, never the transcript body.
    """

    text_ref = _require_safe_ref(text_ref, "text_ref")
    return _LOCAL_TRANSCRIPT_TEXT_BY_REF.get(text_ref)


def resolve_asr_live_model_io_debug(adapter_request_id: str) -> dict[str, Any] | None:
    adapter_request_id = _require_safe_ref(adapter_request_id, "adapter_request_id")
    value = _LOCAL_ASR_MODEL_IO_BY_ADAPTER_REQUEST_ID.get(adapter_request_id)
    return deepcopy(value) if value is not None else None


def _store_local_transcript_projection(
    *,
    adapter_request_id: str,
    transcript_text: str,
) -> tuple[str, str]:
    adapter_request_id = _require_safe_ref(adapter_request_id, "adapter_request_id")
    asr_frame_ref = f"asr-frame://provider/dashscope/{adapter_request_id}"
    text_ref = f"text://provider/dashscope/{adapter_request_id}"
    _LOCAL_TRANSCRIPT_TEXT_BY_REF[text_ref] = transcript_text
    return asr_frame_ref, text_ref


def _build_openai_compatible_asr_request_body(
    *,
    audio_payload: bytes,
    audio_mime_type: str,
    audio_format: str,
    model_alias: str,
) -> dict[str, Any]:
    encoded_audio = base64.b64encode(audio_payload).decode("ascii")
    return {
        "model": model_alias,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:{audio_mime_type};base64,{encoded_audio}",
                            "format": audio_format,
                        },
                    }
                ],
            }
        ],
        "stream": False,
    }


def _store_asr_model_io_request(
    *,
    adapter_request_id: str,
    model_alias: str,
    request_body: Mapping[str, Any],
) -> None:
    _LOCAL_ASR_MODEL_IO_BY_ADAPTER_REQUEST_ID[adapter_request_id] = {
        "adapter": "asr",
        "adapter_request_id": adapter_request_id,
        "model_alias": model_alias,
        "provider_url_ref": ASR_LIVE_DASHSCOPE_PROVIDER_URL_REF,
        "request_body": _redact_model_io_value(request_body),
        "provider_text": None,
        "raw_audio_visible": False,
        "authorization_header_visible": False,
    }


def _store_asr_model_io_response(
    *,
    adapter_request_id: str,
    provider_text: str,
) -> None:
    current = _LOCAL_ASR_MODEL_IO_BY_ADAPTER_REQUEST_ID.setdefault(
        adapter_request_id,
        {
            "adapter": "asr",
            "adapter_request_id": adapter_request_id,
            "provider_url_ref": ASR_LIVE_DASHSCOPE_PROVIDER_URL_REF,
            "request_body": None,
            "raw_audio_visible": False,
            "authorization_header_visible": False,
        },
    )
    current["provider_text"] = _redact_debug_string(provider_text)


def _redact_model_io_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact_model_io_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_model_io_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_model_io_value(item) for item in value]
    if isinstance(value, str):
        if value.startswith("data:"):
            return "[redacted-audio-base64]"
        return _redact_debug_string(value)
    return value


def _redact_debug_string(value: str) -> str:
    redacted = value
    for marker in (
        "Bearer ",
        "authorization:",
        "cookie:",
        "api_" + "key=",
        "token=",
        "file://",
        "/Users/",
        "\\Users\\",
        "/private/",
        ".env",
    ):
        redacted = redacted.replace(marker, "[redacted]")
        redacted = redacted.replace(marker.lower(), "[redacted]")
    return redacted


def _extract_response_text(response_payload: Mapping[str, Any]) -> str:
    choices = response_payload.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            message = first_choice.get("message")
            if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                return str(message["content"])

    output = response_payload.get("output")
    if isinstance(output, Mapping) and isinstance(output.get("text"), str):
        return str(output["text"])

    shape_reasons = [
        "provider_response_text_missing",
        "provider_response_shape_choices_message_content_missing",
    ]
    if not isinstance(output, Mapping) or not isinstance(output.get("text"), str):
        shape_reasons.append("provider_response_shape_output_text_missing")
    raise DashScopeAsrLiveTransportError(
        "provider response text missing",
        failure_reasons=tuple(shape_reasons),
    )


def _response_text_size_bucket(value: str) -> str:
    length = len(value)
    if length == 0:
        return "empty"
    if length <= 200:
        return "small"
    if length <= 2000:
        return "medium"
    return "large"


def _audio_format_from_mime_type(audio_mime_type: str) -> str:
    if audio_mime_type == "audio/wav":
        return "wav"
    if audio_mime_type in {"audio/mp3", "audio/mpeg"}:
        return "mp3"
    raise DashScopeAsrLiveTransportError(
        "unsupported audio mime type",
        failure_reasons=("unsupported_audio_mime_type",),
    )


def _require_audio_payload(value: bytes) -> None:
    if not isinstance(value, bytes) or value == b"":
        raise DashScopeAsrLiveTransportError("audio_payload must be non-empty bytes")


def _require_present_credential_value(value: str | None) -> None:
    if not isinstance(value, str) or value == "":
        raise DashScopeAsrLiveTransportError(
            "credential value missing",
            failure_reasons=("credential value missing",),
        )


def _require_positive_int(value: int, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DashScopeAsrLiveTransportError(f"{field} must be a positive integer")


def _require_safe_ref(value: Any, field: str) -> str:
    if not isinstance(value, str) or value == "":
        raise DashScopeAsrLiveTransportError(f"{field} must be a non-empty string")
    if CREDENTIAL_LIKE_REF_PATTERN.search(value) or _looks_like_local_path(value):
        raise DashScopeAsrLiveTransportError(f"{field} must be a safe ref")
    return value


def _safe_failure_reasons(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        normalized.append(_safe_failure_reason(value))
    return tuple(normalized)


def _safe_failure_reason(value: Any) -> str:
    if not isinstance(value, str) or value == "":
        return "request_failed"
    lowered = value.lower()
    if (
        CREDENTIAL_LIKE_REF_PATTERN.search(value)
        or "raw_provider_body" in lowered
        or "raw_transcript" in lowered
        or "raw_audio" in lowered
    ):
        return "redacted_failure"
    return value


def _http_status_class_category(status_code: int | None) -> str:
    if isinstance(status_code, int):
        status_class = status_code // 100
        if status_class in {1, 2, 3, 4, 5}:
            return f"provider_http_status_class_{status_class}xx"
    return "provider_http_status_class_unknown"


def _looks_like_local_path(value: str) -> bool:
    return (
        value.startswith("/")
        or value.startswith("~/")
        or value.startswith("file://")
        or "\\Users\\" in value
        or "/Users/" in value
    )
