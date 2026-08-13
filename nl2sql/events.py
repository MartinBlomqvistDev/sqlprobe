"""Typed contracts for the server-sent event stream.

The chat endpoint streams one JSON object per SSE data line. Modelling those
events as Pydantic classes means the wire format is defined once, validated on
the way out, and can be imported by a Python client or a test harness instead
of being rediscovered from the transport code.

Wire format, one per `data:` line:

    {"type": "token",       "text": "..."}
    {"type": "tool_call",   "tool": "...", "input": {...}}
    {"type": "tool_result", "tool": "...", "result": {...}}
    {"type": "error",       "message": "..."}
    {"type": "done"}
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Base class for every streamed event."""

    type: str

    def to_sse(self) -> str:
        """Serialise the event as a complete SSE `data:` frame."""
        body = json.dumps(self.model_dump(), ensure_ascii=False, default=str)
        return f"data: {body}\n\n"


class TokenEvent(Event):
    """A fragment of the assistant's natural language answer."""

    type: Literal["token"] = "token"
    text: str


class ToolCallEvent(Event):
    """The model has asked to run a tool, with the arguments it chose."""

    type: Literal["tool_call"] = "tool_call"
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(Event):
    """The full result of a tool call, before any truncation for the model."""

    type: Literal["tool_result"] = "tool_result"
    tool: str
    result: dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(Event):
    """A failure the client should surface instead of an answer."""

    type: Literal["error"] = "error"
    message: str


class DoneEvent(Event):
    """The final event of every stream, successful or not."""

    type: Literal["done"] = "done"


class ChatRequest(BaseModel):
    """Request body accepted by POST /api/chat."""

    question: str = Field(min_length=1, max_length=2000)
    history: list["ChatTurn"] = Field(default_factory=list, max_length=40)


class ChatTurn(BaseModel):
    """One prior turn of the conversation, replayed to give the model context."""

    role: Literal["user", "assistant"]
    content: str


ChatRequest.model_rebuild()
