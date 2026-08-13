"""Async SQLAlchemy access to the analytics database.

This module owns the engine. Every read in the application goes through
run_select(), which is the only place a query reaches the database, so the
guard in sql_guard.py cannot be bypassed by accident.

Engines are created lazily and cached, so importing this module has no side
effects and the tests can point it at a temporary database.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nl2sql.config import get_settings
from nl2sql.sql_guard import enforce_limit, validate_select

logger = logging.getLogger(__name__)

_engines: dict[str, AsyncEngine] = {}


def get_engine(url: str | None = None) -> AsyncEngine:
    """Return a cached async engine for the given database URL.

    Args:
        url: SQLAlchemy URL. Defaults to the configured analytics database.

    Returns:
        A lazily created, process-wide AsyncEngine.
    """
    resolved = url or get_settings().database_url
    engine = _engines.get(resolved)
    if engine is None:
        engine = create_async_engine(resolved, pool_pre_ping=True)
        _engines[resolved] = engine
    return engine


async def dispose_engines() -> None:
    """Close every cached engine. Called on application shutdown."""
    for engine in _engines.values():
        await engine.dispose()
    _engines.clear()


async def run_select(query: str, url: str | None = None) -> dict[str, Any]:
    """Validate, limit and execute a read-only query.

    The query is rejected outright unless it is a single SELECT (or a WITH that
    resolves to one), then rewritten to carry a LIMIT no larger than the
    configured cap before it is sent to the database.

    Args:
        query: The SQL the model produced.
        url: Optional database URL override, used by the tests.

    Returns:
        On success, a dict with `rows`, `columns`, `row_count` and the `query`
        that actually ran. On failure, a dict with an `error` key describing
        what went wrong, phrased so the model can correct itself and retry.
    """
    settings = get_settings()

    is_valid, reason = validate_select(query)
    if not is_valid:
        logger.warning(
            "query blocked: %s", reason, extra={"operation": "sql_blocked"}
        )
        return {"error": f"Query rejected: {reason}", "rows": [], "columns": []}

    safe_query = enforce_limit(query, max_rows=settings.max_rows)
    started = time.perf_counter()

    try:
        engine = get_engine(url)
        async with engine.connect() as connection:
            result = await connection.execute(text(safe_query))
            columns: Sequence[str] = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
    except Exception as exc:  # noqa: BLE001 - surfaced to the model verbatim
        duration_ms = round((time.perf_counter() - started) * 1000)
        logger.error(
            "query failed: %s",
            exc,
            extra={"operation": "sql_error", "duration_ms": duration_ms},
        )
        return {
            "error": (
                f"{exc}\n\nFailed query:\n{safe_query}\n\n"
                "Check the table and column names against the schema, "
                "then try a different approach."
            ),
            "rows": [],
            "columns": [],
            "query": safe_query,
        }

    duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info(
        "query returned %d rows",
        len(rows),
        extra={
            "operation": "sql_ok",
            "row_count": len(rows),
            "duration_ms": duration_ms,
        },
    )
    return {
        "rows": rows,
        "columns": list(columns),
        "row_count": len(rows),
        "query": safe_query,
    }
