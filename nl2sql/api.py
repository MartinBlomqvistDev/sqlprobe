"""The FastAPI surface.

    POST /api/chat                  Ask a question, get an SSE event stream.
    GET  /api/resolve/{ref}         Resolve a pseudonymous reference locally.
    GET  /api/health                Liveness plus a database round trip.

Run it with:

    uvicorn nl2sql.api:app --reload
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from nl2sql import __version__
from nl2sql.agent import answer_question
from nl2sql.config import Settings, get_settings
from nl2sql.db import dispose_engines, get_engine
from nl2sql.events import ChatRequest, ErrorEvent
from nl2sql.logging_setup import request_id_var, setup_logging
from nl2sql.resolver import resolve_reference

setup_logging(get_settings().log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log the active configuration on startup and close engines on shutdown."""
    settings = get_settings()
    logger.info(
        "starting up",
        extra={"operation": "startup", "provider": settings.llm_provider},
    )
    yield
    await dispose_engines()


app = FastAPI(
    title="nl2sql-chat",
    description="Natural language to SQL chat agent",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next: Any) -> Any:
    """Tag every request with a short id and log its start and end."""
    token = request_id_var.set(uuid.uuid4().hex[:8])
    started = time.perf_counter()
    try:
        response = await call_next(request)
        logger.info(
            "%s %s",
            request.method,
            request.url.path,
            extra={
                "operation": "request",
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return response
    finally:
        request_id_var.reset(token)


def require_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Reject the request when API_KEY is configured and the header is wrong.

    Leaving API_KEY empty disables the check, which is the right default for
    local development and the wrong one for anything reachable from a network.

    Raises:
        HTTPException: 401 when the key is required and does not match.
    """
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Report liveness and whether the analytics database answers."""
    settings = get_settings()
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001 - the status is the payload
        database = f"error: {exc}"

    return {
        "status": "healthy" if database == "ok" else "unhealthy",
        "version": __version__,
        "provider": settings.llm_provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {"database": database},
    }


@app.post("/api/chat", dependencies=[Depends(require_api_key)])
async def chat(payload: ChatRequest) -> StreamingResponse:
    """Answer a question, streaming the work as server-sent events.

    Args:
        payload: The question and any prior conversation turns.

    Returns:
        A `text/event-stream` response. Every frame is one JSON object; see
        `nl2sql.events` for the shapes.
    """
    history = [turn.model_dump() for turn in payload.history]

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in answer_question(payload.question, history):
                yield event.to_sse()
        except Exception as exc:  # noqa: BLE001 - never leave a stream hanging
            logger.exception("stream failed")
            yield ErrorEvent(message=f"Server error: {exc}").to_sse()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tells nginx and similar not to buffer, which would defeat
            # streaming entirely.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/resolve/{customer_ref}", dependencies=[Depends(require_api_key)])
async def resolve(customer_ref: str) -> dict[str, str | None]:
    """Resolve a pseudonymous reference to a person, without involving the LLM.

    Args:
        customer_ref: The reference as it appears in the analytics database.

    Returns:
        The directory entry, or the same shape with null values when the
        reference is unknown.
    """
    person = await resolve_reference(customer_ref)
    return person or {
        "customer_ref": customer_ref,
        "full_name": None,
        "contact_email": None,
    }


def main() -> None:
    """Run the development server. Production should invoke uvicorn directly."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
