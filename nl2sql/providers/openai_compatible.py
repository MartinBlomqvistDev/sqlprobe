"""Gemini, Groq and Ollama, via their OpenAI-compatible endpoints.

All three speak the same wire protocol, so one adapter serves them and the
differences reduce to a base URL, a key, a model name and a timeout. Keeping
them together rather than as three near-identical modules means a fix to the
streaming tool-call assembly below is a fix for all three at once.

The fiddly part is that tool calls arrive in fragments: the name may come in
one chunk and the JSON arguments across several more, keyed by index. They are
accumulated into `pending` and only parsed once the stream ends.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from nl2sql.config import Settings
from nl2sql.providers.base import LLMProvider, ProviderError, ToolCall, ToolResult
from nl2sql.tools import TOOLS, Tool

logger = logging.getLogger(__name__)

# Deterministic output matters more than variety when the job is writing SQL.
TEMPERATURE = 0.1


class OpenAICompatibleProvider(LLMProvider):
    """Streaming tool use against any OpenAI-compatible chat completions API."""

    def __init__(
        self,
        system_prompt: str,
        settings: Settings,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        tools: tuple[Tool, ...] = TOOLS,
    ) -> None:
        """Create the client for one specific compatible endpoint.

        Args:
            system_prompt: The assembled schema and rules prompt.
            settings: Resolved application settings.
            name: Short provider name for logs.
            base_url: The endpoint root, including any version path.
            api_key: Key for the endpoint. Ollama ignores it but requires one.
            model: Model identifier as the endpoint names it.
            timeout: Per-request timeout in seconds.
            tools: Tools to expose to the model.
        """
        super().__init__(system_prompt=system_prompt, tools=tools)
        from openai import AsyncOpenAI

        self.name = name
        self._settings = settings
        self._model = model
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            # Rate limits are handled explicitly rather than by silent backoff,
            # so a slow answer is never a hidden retry the user paid for.
            max_retries=0,
            timeout=timeout,
        )
        self._messages: list[dict[str, Any]] = []
        self._tool_payload = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]

    def seed(self, history: list[dict[str, str]], question: str) -> None:
        """Initialise the conversation with prior turns and the new question."""
        self._messages = [{"role": "system", "content": self.system_prompt}]
        self._messages.extend(
            {"role": turn["role"], "content": turn["content"]} for turn in history
        )
        self._messages.append({"role": "user", "content": question})

    async def stream_turn(self) -> AsyncIterator[str]:
        """Stream one assistant turn and reassemble any tool calls it makes."""
        self._pending = []
        text_parts: list[str] = []
        # index -> {"id": str, "name": str, "arguments": str}
        partial: dict[int, dict[str, str]] = {}

        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=self._messages,
                tools=self._tool_payload,
                stream=True,
                temperature=TEMPERATURE,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue

                if delta.content:
                    text_parts.append(delta.content)
                    yield delta.content

                for fragment in delta.tool_calls or []:
                    slot = partial.setdefault(
                        fragment.index,
                        {
                            "id": fragment.id or f"call_{fragment.index}",
                            "name": "",
                            "arguments": "",
                        },
                    )
                    if fragment.id:
                        slot["id"] = fragment.id
                    if fragment.function and fragment.function.name:
                        slot["name"] = fragment.function.name
                    if fragment.function and fragment.function.arguments:
                        slot["arguments"] += fragment.function.arguments

        except Exception as exc:  # noqa: BLE001 - normalised into one message
            raise ProviderError(self._explain(exc)) from exc

        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts) or None,
        }
        if partial:
            assistant["tool_calls"] = [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {
                        "name": slot["name"],
                        "arguments": slot["arguments"] or "{}",
                    },
                }
                for _, slot in sorted(partial.items())
            ]
        self._messages.append(assistant)

        for _, slot in sorted(partial.items()):
            self._pending.append(
                ToolCall(
                    id=slot["id"],
                    name=slot["name"],
                    arguments=_parse_arguments(slot["arguments"]),
                )
            )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Append one tool message per result, as the protocol expects."""
        self._messages.extend(
            {
                "role": "tool",
                "tool_call_id": result.call_id,
                "content": result.content,
            }
            for result in results
        )

    def _explain(self, exc: Exception) -> str:
        """Turn a provider exception into something worth showing a user."""
        detail = str(exc)
        lowered = detail.lower()
        if "429" in detail or "rate limit" in lowered or "quota" in lowered:
            return (
                f"The {self.name} rate limit was reached. Wait a few seconds "
                "and try again."
            )
        if "timeout" in lowered or "timed out" in lowered:
            return f"The {self.name} request timed out. Try a simpler question."
        if "connection" in lowered:
            return f"Could not reach {self.name}. Check that it is running."
        logger.error("%s call failed: %s", self.name, exc, extra={"provider": self.name})
        return f"The {self.name} call failed: {exc}"


def _parse_arguments(raw: str) -> dict[str, Any]:
    """Parse streamed tool arguments, tolerating a malformed fragment."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("could not parse tool arguments: %.200s", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}
