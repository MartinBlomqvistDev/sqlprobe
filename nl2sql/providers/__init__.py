"""One interface over four LLM providers.

The agent loop never imports a vendor SDK. It talks to LLMProvider, and the
provider named by LLM_PROVIDER is constructed here. Swapping between a hosted
frontier model and a local one is an environment variable, not a code change,
which is what makes the same template usable for a production deployment, a
free-tier development loop and an offline demo.

    anthropic   Claude, via the Anthropic SDK.
    gemini      Google, via its OpenAI-compatible endpoint.
    groq        Groq, via its OpenAI-compatible endpoint.
    ollama      A local model, via Ollama's OpenAI-compatible endpoint.

The three OpenAI-compatible providers share a single adapter that differs only
in base URL, key and model, so adding another one is a few lines in
build_provider below.
"""

from __future__ import annotations

from nl2sql.config import Settings, get_settings
from nl2sql.providers.base import (
    LLMProvider,
    ProviderError,
    ToolCall,
    ToolResult,
)

__all__ = [
    "LLMProvider",
    "ProviderError",
    "ToolCall",
    "ToolResult",
    "build_provider",
]


def build_provider(
    system_prompt: str,
    settings: Settings | None = None,
) -> LLMProvider:
    """Construct the provider named by the configuration.

    Args:
        system_prompt: The assembled system prompt for this request.
        settings: Optional settings override, used by the tests.

    Returns:
        A ready-to-use provider instance.

    Raises:
        ProviderError: If the provider name is unknown, or its API key is
            missing.
    """
    from nl2sql.providers.anthropic_provider import AnthropicProvider
    from nl2sql.providers.openai_compatible import OpenAICompatibleProvider

    resolved = settings or get_settings()
    name = resolved.llm_provider

    if name == "anthropic":
        if not resolved.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set.")
        return AnthropicProvider(system_prompt=system_prompt, settings=resolved)

    if name == "gemini":
        if not resolved.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY is not set.")
        return OpenAICompatibleProvider(
            system_prompt=system_prompt,
            settings=resolved,
            name="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=resolved.gemini_api_key,
            model=resolved.gemini_model,
            timeout=90.0,
        )

    if name == "groq":
        if not resolved.groq_api_key:
            raise ProviderError("GROQ_API_KEY is not set.")
        return OpenAICompatibleProvider(
            system_prompt=system_prompt,
            settings=resolved,
            name="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=resolved.groq_api_key,
            model=resolved.groq_model,
            timeout=30.0,
        )

    if name == "ollama":
        return OpenAICompatibleProvider(
            system_prompt=system_prompt,
            settings=resolved,
            name="ollama",
            base_url=f"{resolved.ollama_base_url.rstrip('/')}/v1",
            # Ollama ignores the key but the client requires a non-empty value.
            api_key="ollama",
            model=resolved.ollama_model,
            timeout=120.0,
        )

    raise ProviderError(f"Unknown provider: {name}")
