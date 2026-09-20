"""Real-API recognition tests — SKIPPED by default.

These hit OpenAI / Anthropic over the public internet. They are gated
behind both an env var (``RUN_REAL_AI_TESTS=1``) AND the presence of a
valid API key. Default ``pytest`` runs do not touch the network.

Enable with::

    RUN_REAL_AI_TESTS=1 AI_PROVIDER=openai_compatible AI_API_KEY=sk-... \\
        pytest tests/api/test_recognition_real_api.py -v
"""
from __future__ import annotations

import io
import os

import pytest
from PIL import Image

from app.ai.factory import get_provider, reset_provider
from app.ai.provider import VisionOutput


def _png_bytes() -> bytes:
    img = Image.new("RGB", (16, 16), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _should_run() -> bool:
    return (
        os.environ.get("RUN_REAL_AI_TESTS") == "1"
        and bool(os.environ.get("AI_API_KEY"))
        and "sk-xxxxx" not in os.environ.get("AI_API_KEY", "")
    )


pytestmark = pytest.mark.skipif(
    not _should_run(),
    reason="Real AI tests require RUN_REAL_AI_TESTS=1 and a valid AI_API_KEY",
)


@pytest.mark.asyncio
async def test_real_vision_returns_valid_output() -> None:
    """End-to-end smoke test against the configured provider."""
    reset_provider()
    provider = get_provider()
    # We don't pass a real URL — the mock fixture image is a tiny PNG.
    # We just want to verify the provider returns a VisionOutput-shaped
    # object and that the schema round-trips.
    try:
        out: VisionOutput = await provider.vision(
            _png_bytes_url(),  # type: ignore[arg-type]
            hint="a small object",
        )
    except NotImplementedError:
        pytest.skip("Provider doesn't implement vision()")
    assert isinstance(out, VisionOutput)
    assert 0 < len(out.name) <= 128


def _png_bytes_url() -> str:
    """Return a tiny data: URI so the provider can fetch the image."""
    import base64

    raw = _png_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
