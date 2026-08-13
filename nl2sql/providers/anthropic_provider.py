"""Claude, via the Anthropic SDK.

Three details are worth knowing if you change this file.

**Prompt caching.** The system prompt is sent as a content block marked
`cache_control: ephemeral`. The schema, rules and examples are identical on
every request, so after the first call that prefix is read back from cache at a
fraction of the input price. Caching is a prefix match, so anything that varies
per request must come after it: never interpolate a timestamp, a user id or a
request id into the system prompt.

**Assistant content is echoed back verbatim.** The response content is appended
to the conversation exactly as it arrived rather than being rebuilt from the
text. Current models return reasoning blocks that must survive the round trip
unchanged, and reconstructing the turn from its text alone silently drops them.

**Sampling parameters are not sent.** Current Claude models reject
`temperature`, `top_p` and `top_k`. Depth is controlled with `effort` instead,
which is exposed as ANTHROPIC_EFFORT.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from nl2sql.config import Settings
from nl2sql.providers.base import LLMProvider, ProviderError, ToolCall, ToolResult
from nl2sql.tools import TOOLS, Tool

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Streaming tool use against the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        system_prompt: str,
        settings: Settings,
        tools: tuple[Tool, ...] = TOOLS,
    ) -> None:
        """Create the client and freeze the request-invariant payloads.

        Args:
            system_prompt: The assembled schema and rules prompt.
            settings: Resolved application settings.
            tools: Tools to expose to the model.
        """
        super().__init__(system_prompt=system_prompt, tools=tools)
        from anthropic import AsyncAnthropic

        self._settings = settings
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._messages: list[dict[str, Any]] = []

        # Marked for caching: this block is byte-identical between requests.
        self._system: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        self._tool_payload = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ]

    def seed(self, history: list[dict[str, str]], question: str) -> None:
        """Initialise the conversation with prior turns and the new question."""
        self._messages = [
            {"role": turn["role"], "content": turn["content"]} for turn in history
        ]
        self._messages.append({"role": "user", "content": question})

    async def stream_turn(self) -> AsyncIterator[str]:
        """Stream one assistant turn and collect any tool calls it makes."""
        from anthropic import APIError

        self._pending = []
        request: dict[str, Any] = {
            "model": self._settings.anthropic_model,
            "max_tokens": self._settings.max_output_tokens,
            "system": self._system,
            "messages": self._messages,
            "tools": self._tool_payload,
        }
        if self._settings.anthropic_effort:
            request["output_config"] = {"effort": self._settings.anthropic_effort}

        try:
            async with self._client.messages.stream(**request) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and (
                        getattr(event.delta, "type", None) == "text_delta"
                    ):
                        yield event.delta.text
                final = await stream.get_final_message()
        except APIError as exc:
            logger.error("anthropic api error: %s", exc, extra={"provider": self.name})
            raise ProviderError(f"The model API returned an error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - surfaced as a stream error
            logger.error("anthropic call failed: %s", exc, extra={"provider": self.name})
            raise ProviderError(f"The model call failed: {exc}") from exc

        # A safety decline arrives as a normal response, so check before use.
        if final.stop_reason == "refusal":
            raise ProviderError(
                "The model declined to answer this question. Try rephrasing it."
            )

        # Echoed unchanged: rebuilding this from text would drop reasoning
        # blocks the API requires back verbatim on the next turn.
        self._messages.append({"role": "assistant", "content": final.content})

        for block in final.content:
            if getattr(block, "type", None) == "tool_use":
                self._pending.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Append tool outcomes as a single user turn of tool_result blocks."""
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call_id,
                        "content": result.content,
                    }
                    for result in results
                ],
            }
        )
