"""The tool-use loop.

One function, one shape, every provider:

    the model answers, or asks for a tool
    the tool runs
    the full result goes to the client, a reduced copy goes back to the model
    repeat until the model stops asking for tools

Everything the loop yields is a typed event from `events`, so the transport
layer only has to write strings to a socket, and a test harness can assert on
the same objects the browser receives.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

from nl2sql.config import Settings, get_settings
from nl2sql.events import (
    DoneEvent,
    ErrorEvent,
    Event,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from nl2sql.providers import ProviderError, ToolResult, build_provider
from nl2sql.schema_context import build_system_prompt
from nl2sql.tools import ToolBox, summarise_for_model

logger = logging.getLogger(__name__)


async def answer_question(
    question: str,
    history: list[dict[str, str]] | None = None,
    settings: Settings | None = None,
) -> AsyncIterator[Event]:
    """Answer one question, streaming events as the work happens.

    Args:
        question: The user's natural language question.
        history: Prior turns as `{"role": ..., "content": ...}` mappings.
        settings: Optional settings override, used by the tests.

    Yields:
        TokenEvent for answer text, ToolCallEvent and ToolResultEvent around
        every tool invocation, at most one ErrorEvent, and always a final
        DoneEvent.
    """
    resolved = settings or get_settings()
    started = time.perf_counter()
    toolbox = ToolBox()

    try:
        provider = build_provider(build_system_prompt(), resolved)
    except ProviderError as exc:
        yield ErrorEvent(message=str(exc))
        yield DoneEvent()
        return

    provider.seed(history or [], question)
    logger.info(
        "question received",
        extra={
            "operation": "chat_start",
            "provider": provider.name,
            "question_preview": question[:80],
        },
    )

    try:
        for iteration in range(resolved.max_tool_iterations):
            async for text in provider.stream_turn():
                yield TokenEvent(text=text)

            calls = provider.pending_tool_calls
            if not calls:
                break

            results: list[ToolResult] = []
            for call in calls:
                yield ToolCallEvent(tool=call.name, input=call.arguments)
                result = await toolbox.run(call.name, call.arguments)
                # The client gets everything, the model gets a reduced copy.
                yield ToolResultEvent(tool=call.name, result=result)
                results.append(
                    ToolResult(
                        call_id=call.id,
                        name=call.name,
                        content=json.dumps(
                            summarise_for_model(result),
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                )

            provider.add_tool_results(results)
        else:
            # The loop ran to its limit without the model settling on an answer.
            logger.warning(
                "tool iteration limit reached",
                extra={"operation": "chat_truncated", "provider": provider.name},
            )
            yield ErrorEvent(
                message=(
                    "The question needed more steps than the configured limit "
                    "allows. Try asking something narrower."
                )
            )

    except ProviderError as exc:
        yield ErrorEvent(message=str(exc))
    except Exception as exc:  # noqa: BLE001 - a stream must always terminate
        logger.exception("unhandled error in the agent loop")
        yield ErrorEvent(message=f"Something went wrong: {exc}")

    duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info(
        "question answered",
        extra={
            "operation": "chat_done",
            "provider": provider.name,
            "duration_ms": duration_ms,
        },
    )
    yield DoneEvent()
