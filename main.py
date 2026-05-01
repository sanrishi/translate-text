from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
import httpx
from siglume_api_sdk import ExecutionContext, ExecutionKind

from adapter import build_app


app = FastAPI(title="translate-text", version="0.1.0")
_ADAPTER = build_app()


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        resp = httpx.get(
            "https://api.mymemory.translated.net/get",
            params={"q": "hello", "langpair": "en|ja"},
            timeout=10.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Upstream healthcheck failed: {exc!r}")

    manifest = _ADAPTER.manifest()
    return {
        "ok": True,
        "service": "translate-text",
        "capability_key": manifest.capability_key,
        "upstream_ok": True,
    }


@app.post("/invoke")
async def invoke(
    request: Request,
    x_siglume_review_key: str | None = Header(default=None, alias="X-Siglume-Review-Key"),
) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    ctx = ExecutionContext(
        agent_id="vercel",
        owner_user_id="siglume",
        task_type="translate_text",
        input_params=body,
        execution_kind=ExecutionKind.ACTION,
    )
    result = await _ADAPTER.execute(ctx)
    if not result.success:
        raise HTTPException(status_code=500, detail=getattr(result, "error_message", None) or "Execution failed")
    return result.output
