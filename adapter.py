"""DeepL Translate Text — capability_key=deepl-translate-text.

Read-only adapter that translates a single text string with the DeepL API.
The source language is auto-detected by DeepL; callers provide a target
language code.

DeepL API reference:
  https://developers.deepl.com/api-reference/translate
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

CAPABILITY_KEY = "deepl-translate-text"
SOURCE = "DeepL API"
SOURCE_URL = "https://developers.deepl.com/api-reference/translate"

DEFAULT_TIMEOUT_SECONDS = 12.0

_FORMALITY_CHOICES = ("default", "more", "less", "prefer_more", "prefer_less")
_SPLIT_SENTENCES_CHOICES = ("0", "1", "nonewlines")


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


def require_choice(value: Any, *, field: str, choices: tuple[str, ...]) -> str:
    s = require_str(value, field=field)
    if s not in choices:
        raise InvalidInputError(
            f"`{field}` must be one of {list(choices)}, got {s!r}."
        )
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


def _require_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise InvalidInputError(f"`{field}` must be a boolean.")


def _normalize_target_lang(value: Any) -> str:
    s = require_str(value, field="target_lang", max_len=16).upper()
    # DeepL uses ISO 639-1 + optional region variant, e.g. EN, EN-US, PT-BR.
    # Keep validation intentionally loose to avoid breaking new codes.
    if not s.replace("-", "").isalpha() or not (2 <= len(s) <= 8):
        raise InvalidInputError(
            "`target_lang` must look like a DeepL language code such as "
            "'EN', 'JA', 'EN-US', or 'PT-BR'."
        )
    return s


def _auth_key() -> str | None:
    return (
        os.environ.get("DEEPL_AUTH_KEY")
        or os.environ.get("DEEPL_API_KEY")
        or os.environ.get("DEEPL_APIKEY")
    )


def _base_url(auth_key: str) -> str:
    override = os.environ.get("DEEPL_API_BASE_URL") or os.environ.get("DEEPL_BASE_URL")
    if override:
        return override.rstrip("/")
    # DeepL Free auth keys typically end with ":fx" and use api-free.
    if auth_key.strip().endswith(":fx"):
        return "https://api-free.deepl.com"
    return "https://api.deepl.com"


def do_translate(input_params: dict[str, Any], *, http=fetch_json) -> dict[str, Any]:
    text = require_str(input_params.get("text"), field="text", max_len=20_000)
    target_lang = _normalize_target_lang(input_params.get("target_lang"))

    preserve_formatting: bool | None = None
    if "preserve_formatting" in input_params:
        preserve_formatting = _require_bool(
            input_params.get("preserve_formatting"), field="preserve_formatting"
        )

    formality: str | None = None
    if "formality" in input_params and input_params.get("formality") is not None:
        formality = require_choice(
            input_params.get("formality"),
            field="formality",
            choices=_FORMALITY_CHOICES,
        )

    split_sentences: str | None = None
    if "split_sentences" in input_params and input_params.get("split_sentences") is not None:
        split_sentences = require_choice(
            input_params.get("split_sentences"),
            field="split_sentences",
            choices=_SPLIT_SENTENCES_CHOICES,
        )

    key = _auth_key()
    if not key:
        raise InvalidInputError(
            "Missing DeepL API key. Set `DEEPL_AUTH_KEY` (or `DEEPL_API_KEY`) "
            "in the environment."
        )

    url = f"{_base_url(key)}/v2/translate"
    timeout = float(os.environ.get("DEEPL_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS)

    body: dict[str, Any] = {
        "text": [text],
        "target_lang": target_lang,
        "show_billed_characters": True,
    }
    if preserve_formatting is not None:
        body["preserve_formatting"] = preserve_formatting
    if formality is not None:
        body["formality"] = formality
    if split_sentences is not None:
        body["split_sentences"] = split_sentences

    payload = http(
        url,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"DeepL-Auth-Key {key}",
        },
        json_body=body,
        timeout=timeout,
        retries=2,
    )

    translations = payload.get("translations")
    if not isinstance(translations, list) or not translations:
        raise EmptyResultError("DeepL returned no translations.")
    first = translations[0] if isinstance(translations[0], dict) else None
    if not first or "text" not in first:
        raise EmptyResultError("DeepL returned an unexpected response shape.")

    translated_text = first.get("text")
    detected_source_lang = first.get("detected_source_language")
    billed_characters = payload.get("billed_characters")

    out: dict[str, Any] = {
        "input_text": text,
        "translated_text": translated_text,
        "detected_source_lang": detected_source_lang,
        "target_lang": target_lang,
    }
    if billed_characters is not None:
        out["billed_characters"] = billed_characters

    summary = f"Translated {len(text)} chars to {target_lang} via DeepL."
    return with_envelope(
        out,
        summary=summary,
        source=SOURCE,
        source_url=SOURCE_URL,
        cache_ttl_seconds=0,
        attribution="DeepL",
    )


class DeepLTranslateTextApp(AppAdapter):
    def manifest(self) -> AppManifest:
        return AppManifest(
            capability_key=CAPABILITY_KEY,
            name="DeepL Translate Text",
            job_to_be_done=(
                "Translate a short text string into a target language using DeepL, "
                "with automatic source-language detection."
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
            short_description="Translate text with DeepL (auto-detect source language).",
            description=(
                "Calls the DeepL API /v2/translate endpoint to translate a single "
                "UTF-8 text string. The adapter does not require a source language; "
                "DeepL auto-detects it. Supports optional `formality`, "
                "`split_sentences`, and `preserve_formatting` parameters."
            ),
            docs_url=(
                "https://github.com/taihei-05/siglume-personal-apis/tree/main/"
                "apis/translation/translate_text"
            ),
            support_contact="https://github.com/taihei-05/siglume-personal-apis/issues",
            compatibility_tags=["translation", "deepl", "language", "read-only"],
            example_prompts=[
                "Translate 'Hello, world!' to Japanese.",
                "Translate this email to German with a more formal tone.",
            ],
        )

    async def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        try:
            if ctx.execution_kind == ExecutionKind.DRY_RUN:
                params = ctx.input_params or {}
                text = str(params.get("text") or "Hello, world!")
                target = str(params.get("target_lang") or "JA").upper()
                output = with_envelope(
                    {
                        "input_text": text,
                        "translated_text": f"[dry_run] ({target}) {text}",
                        "detected_source_lang": "EN",
                        "target_lang": target,
                        "billed_characters": len(text),
                    },
                    summary=f"[dry_run] Translated {len(text)} chars to {target}.",
                    source=SOURCE,
                    source_url=SOURCE_URL,
                    cache_ttl_seconds=0,
                    attribution="DeepL",
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


# Hook for `siglume test .` — the harness picks up the first AppAdapter in the
# module. We also expose a callable for the FastAPI server.
def build_app() -> DeepLTranslateTextApp:
    return DeepLTranslateTextApp()
