"""MyMemory Translate Text — capability_key=mymemory-translate-text.

Read-only adapter that translates a single UTF-8 text string with the
MyMemory Translation API.

Endpoint:
  GET https://api.mymemory.translated.net/get?q=<text>&langpair=<src>|<tgt>
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
from siglume_api_sdk import (
    AppAdapter,
    AppCategory,
    AppManifest,
    ApprovalMode,
    ExecutionContext,
    ExecutionKind,
    ExecutionResult,
    PermissionClass,
    PriceModel,
)

CAPABILITY_KEY = "mymemory-translate-text"
SOURCE = "MyMemory Translation API"
SOURCE_URL = "https://api.mymemory.translated.net/get"

DEFAULT_TIMEOUT_SECONDS = 12.0


class AdapterError(Exception):
    error_code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self, *, source: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "error": True,
            "error_code": self.error_code,
            "message": self.message,
        }
        if self.details is not None:
            out["details"] = self.details
        if source:
            out["source"] = source
        return out


class InvalidInputError(AdapterError):
    error_code = "invalid_input"
    http_status = 400


class EmptyResultError(AdapterError):
    error_code = "empty_result"
    http_status = 200


class UpstreamUnavailableError(AdapterError):
    error_code = "upstream_unavailable"
    http_status = 503


class UpstreamRateLimitedError(AdapterError):
    error_code = "upstream_rate_limited"
    http_status = 429


class UpstreamNotFoundError(AdapterError):
    error_code = "upstream_not_found"
    http_status = 404


class UpstreamTimeoutError(AdapterError):
    error_code = "upstream_timeout"
    http_status = 504


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def with_envelope(
    payload: dict[str, Any],
    *,
    source: str,
    source_url: str,
    cache_ttl_seconds: int,
    attribution: str | None = None,
    fetched_at: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = dict(payload)
    if summary is not None:
        out.setdefault("summary", summary)
    out["source"] = source
    out["source_url"] = source_url
    out["fetched_at"] = fetched_at or _utc_now_iso()
    out["cache_ttl_seconds"] = cache_ttl_seconds
    if attribution:
        out["attribution"] = attribution
    return out


def require_str(value: Any, *, field: str, max_len: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"`{field}` must be a non-empty string.")
    if len(value) > max_len:
        raise InvalidInputError(f"`{field}` exceeds max length {max_len}.")
    return value.strip()


def _normalize_lang(value: Any | None, *, field: str, default: str | None) -> str:
    if value is None:
        if default is None:
            raise InvalidInputError(f"`{field}` is required.")
        return default
    s = require_str(value, field=field, max_len=16).lower()
    # Keep validation loose (MyMemory uses ISO-like codes).
    if not s.replace("-", "").isalpha() or len(s) < 2:
        raise InvalidInputError(f"`{field}` must look like a language code.")
    return s


def fetch_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any | None = None,
    timeout: float = 8.0,
    retries: int = 2,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.request(
                    method.upper(),
                    url,
                    headers=dict(headers or {}),
                    params=dict(params or {}),
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            last_exc = UpstreamTimeoutError(f"Timed out calling {url} after {timeout}s")
            if attempt + 1 < retries:
                continue
            raise last_exc from exc
        except httpx.HTTPError as exc:
            last_exc = UpstreamUnavailableError(f"HTTP error calling {url}: {exc!r}")
            if attempt + 1 < retries:
                continue
            raise last_exc from exc

        if 200 <= resp.status_code < 300:
            try:
                return resp.json()
            except Exception as exc:
                raise UpstreamUnavailableError(
                    f"Upstream returned non-JSON for {url}: {exc!r}"
                ) from exc

        message = f"{method.upper()} {url}"
        if resp.status_code == 404:
            raise UpstreamNotFoundError(f"Upstream returned 404 for {message}")
        if resp.status_code == 429:
            raise UpstreamRateLimitedError(f"Upstream rate-limited: {message}")
        if resp.status_code in (500, 502, 503, 504) and attempt + 1 < retries:
            continue
        raise UpstreamUnavailableError(f"Upstream {resp.status_code} on {message}")

    if last_exc:
        raise last_exc
    raise UpstreamUnavailableError(f"Unknown failure calling {url}")


def do_translate(input_params: dict[str, Any], *, http=fetch_json) -> dict[str, Any]:
    text = require_str(input_params.get("text"), field="text", max_len=20_000)
    source_lang = _normalize_lang(input_params.get("source_lang"), field="source_lang", default="en")
    target_lang = _normalize_lang(input_params.get("target_lang"), field="target_lang", default=None)

    timeout = float(os.environ.get("MYMEMORY_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)
    payload = http(
        SOURCE_URL,
        method="GET",
        params={"q": text, "langpair": f"{source_lang}|{target_lang}"},
        timeout=timeout,
        retries=2,
    )

    try:
        translated_text = payload["responseData"]["translatedText"]
    except Exception as exc:
        raise EmptyResultError("MyMemory returned an unexpected response shape.") from exc
    if not isinstance(translated_text, str):
        raise EmptyResultError("MyMemory returned a non-string translation.")

    out: dict[str, Any] = {
        "input_text": text,
        "translated_text": translated_text,
        "source_lang": source_lang,
        "target_lang": target_lang,
    }
    summary = f"Translated {len(text)} chars {source_lang}->{target_lang} via MyMemory."
    return with_envelope(
        out,
        summary=summary,
        source=SOURCE,
        source_url=SOURCE_URL,
        cache_ttl_seconds=0,
        attribution="MyMemory",
    )


class MyMemoryTranslateTextApp(AppAdapter):
    def manifest(self) -> AppManifest:
        return AppManifest(
            capability_key=CAPABILITY_KEY,
            name="MyMemory Translate Text",
            job_to_be_done=(
                "Translate a short text string into a target language using the "
                "MyMemory Translation API."
            ),
            category=AppCategory.COMMUNICATION,
            permission_class=PermissionClass.READ_ONLY,
            approval_mode=ApprovalMode.AUTO,
            dry_run_supported=True,
            required_connected_accounts=[],
            price_model=PriceModel.SUBSCRIPTION,
            price_value_minor=500,
            currency="USD",
            jurisdiction="US",
            data_residency="US",
            short_description="Translate text with MyMemory (defaults source_lang to en).",
            description=(
                "Calls MyMemory's public translation endpoint. Provide `text` and "
                "`target_lang` (e.g. 'ja'); `source_lang` defaults to 'en'."
            ),
            docs_url="https://github.com/sanrishi/translate-text",
            support_contact="https://github.com/sanrishi/translate-text/issues",
            compatibility_tags=["translation", "mymemory", "language", "read-only"],
            example_prompts=[
                "Translate 'hello' to Japanese.",
                "Translate this sentence from English to French.",
            ],
        )

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        try:
            if ctx.execution_kind == ExecutionKind.DRY_RUN:
                params = ctx.input_params or {}
                text = str(params.get("text") or "hello")
                source_lang = str(params.get("source_lang") or "en").lower()
                target_lang = str(params.get("target_lang") or "ja").lower()
                output = with_envelope(
                    {
                        "input_text": text,
                        "translated_text": f"[dry_run] ({source_lang}->{target_lang}) {text}",
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                    },
                    summary=f"[dry_run] Translated {len(text)} chars {source_lang}->{target_lang}.",
                    source=SOURCE,
                    source_url=SOURCE_URL,
                    cache_ttl_seconds=0,
                    attribution="MyMemory",
                )
            else:
                output = do_translate(ctx.input_params or {})
        except AdapterError as exc:
            return ExecutionResult(
                success=False,
                execution_kind=ctx.execution_kind,
                output=exc.to_payload(source=SOURCE),
                error_message=exc.message,
            )
        return ExecutionResult(
            success=True,
            execution_kind=ctx.execution_kind,
            output=output,
            units_consumed=1,
        )

    def supported_task_types(self) -> list[str]:
        return ["translate_text", "translation"]


def build_app() -> MyMemoryTranslateTextApp:
    return MyMemoryTranslateTextApp()

