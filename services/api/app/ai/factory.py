"""AIProvider factory.

Selects a concrete provider based on ``settings.ai_provider``. Business
code MUST go through :func:`get_provider` (or the cached singleton via
:data:`provider`) rather than importing any concrete provider class
directly — that way swapping vendors is a config change, not a code
change.

Provider registry
-----------------

==================  ============================  ======================
``AI_PROVIDER``     Class                          Notes
==================  ============================  ======================
``mock``            :class:`MockAIProvider`       Tests + local dev
``openai_compatible``:class:`OpenAICompatibleProvider` OpenAI / DeepSeek /
                                                       智谱 / 通义 …
``anthropic``       :class:`AnthropicProvider`    Claude
==================  ============================  ======================
"""
from __future__ import annotations

from functools import lru_cache

from app.ai.provider import AIProvider
from app.core.config import settings


@lru_cache(maxsize=1)
def get_provider() -> AIProvider:
    """Return the configured :class:`AIProvider`.

    Cached so the underlying HTTP clients are reused for the lifetime
    of the process. Tests that want a fresh provider should call
    :func:`reset_provider` first.
    """
    name = settings.ai_provider
    if name == "mock":
        # Imported lazily so the real providers don't pull httpx in
        # for tests that only want the mock.
        from app.ai.providers.mock import MockAIProvider

        return MockAIProvider()
    if name == "openai_compatible":
        from app.ai.providers.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider()
    if name == "anthropic":
        from app.ai.providers.anthropic import AnthropicProvider

        return AnthropicProvider()
    raise ValueError(f"Unknown AI_PROVIDER: {name!r}")


def reset_provider() -> None:
    """Drop the cached provider. Tests use this between cases."""
    get_provider.cache_clear()


# Convenience alias for code that just wants the singleton.
provider = get_provider


__all__ = ["get_provider", "provider", "reset_provider"]
