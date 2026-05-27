"""
Tests for lib/services/vision_service.py

Covers:
- _resolve_image_urls: HTTP URLs are downloaded and base64-encoded
- _resolve_image_urls: already-base64 strings pass through unchanged
- _resolve_image_urls: failed download is skipped gracefully
- preprocess_messages: vision disabled → images stripped
- preprocess_messages: model has vision → images passed through (as base64)
- preprocess_messages: model lacks vision → fallback provider called
- _strip_images: all messages have images removed
"""

import asyncio
import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.services.vision_service import VisionService
from lib.ai_providers.base_provider import Message, ChatResponse


FAKE_IMG_BYTES = b"\xff\xd8\xff\xe0test_jpeg_bytes"
FAKE_B64 = base64.b64encode(FAKE_IMG_BYTES).decode()


def _make_service(enabled=True, fallback_provider="ollama", fallback_model="llama3.2-vision:11b"):
    return VisionService({
        "enabled": enabled,
        "fallback_provider": fallback_provider,
        "fallback_model": fallback_model,
        "max_image_size": 5242880,
    })


def _make_messages_with_url():
    return [Message(role="user", content="what's in this?", images=["https://example.com/img.jpg"])]


def _make_messages_with_b64():
    return [Message(role="user", content="describe", images=[FAKE_B64])]


# ---------------------------------------------------------------------------
# _resolve_image_urls
# ---------------------------------------------------------------------------

def test_url_downloaded_and_base64_encoded():
    """HTTP URL image is downloaded and converted to base64."""
    service = _make_service()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.read = AsyncMock(return_value=FAKE_IMG_BYTES)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    async def run():
        with patch("lib.services.vision_service.aiohttp.ClientSession", return_value=mock_session):
            with patch("lib.services.vision_service._is_safe_url", return_value=True):
                return await service._resolve_image_urls(_make_messages_with_url())

    result = asyncio.run(run())
    assert result[0].images and result[0].images[0] == FAKE_B64


def test_base64_passthrough():
    """Already-base64 strings are passed through without any HTTP call."""
    service = _make_service()
    msgs = _make_messages_with_b64()

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(side_effect=AssertionError("get() should not be called for base64"))

    async def run():
        with patch("lib.services.vision_service.aiohttp.ClientSession", return_value=mock_session):
            return await service._resolve_image_urls(msgs)

    result = asyncio.run(run())
    assert result[0].images == [FAKE_B64]


def test_failed_download_skipped_gracefully():
    """A download error skips the image rather than crashing the request."""
    service = _make_service()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(side_effect=aiohttp.ClientError("connection refused"))
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    async def run():
        with patch("lib.services.vision_service.aiohttp.ClientSession", return_value=mock_session):
            with patch("lib.services.vision_service._is_safe_url", return_value=True):
                return await service._resolve_image_urls(_make_messages_with_url())

    result = asyncio.run(run())
    # Message still exists but with no images (download failed, skipped)
    assert result[0].images is None


def test_ssrf_blocked_internal_urls():
    """Internal/private URLs are blocked by SSRF protection before any HTTP request."""
    service = _make_service()

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(side_effect=AssertionError("get() must NOT be called for blocked URLs"))

    blocked_urls = [
        ("http://127.0.0.1:8080/img.jpg", "localhost IP"),
        ("http://localhost/img.jpg", "localhost"),
        ("http://169.254.169.254/latest/meta-data", "cloud metadata"),
        ("http://10.0.0.1/img.jpg", "10.x private range"),
        ("http://192.168.1.1/img.jpg", "192.168.x private range"),
    ]

    for url, label in blocked_urls:
        async def run(u=url):
            with patch("lib.services.vision_service.aiohttp.ClientSession", return_value=mock_session):
                return await service._resolve_image_urls([Message(role="user", content=label, images=[u])])

        result = asyncio.run(run())
        assert result[0].images is None



def test_ssrf_allows_safe_public_urls():
    """Safe public URLs pass SSRF check and proceed to download."""
    service = _make_service()

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.read = AsyncMock(return_value=FAKE_IMG_BYTES)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    safe_url = "https://cdn.example.com/photos/img.jpg"
    msgs = [Message(role="user", content="check this", images=[safe_url])]

    async def run():
        with patch("lib.services.vision_service.aiohttp.ClientSession", return_value=mock_session):
            with patch("lib.services.vision_service._is_safe_url", return_value=True):
                return await service._resolve_image_urls(msgs)

    result = asyncio.run(run())
    assert result[0].images == [FAKE_B64]


# ---------------------------------------------------------------------------
# preprocess_messages
# ---------------------------------------------------------------------------

def test_vision_disabled_strips_images():
    """When vision is disabled all images are removed."""
    service = _make_service(enabled=False)

    async def run():
        return await service.preprocess_messages(_make_messages_with_b64(), model_has_vision=True)

    result = asyncio.run(run())
    assert result[0].images is None


def test_model_has_vision_passthrough():
    """When model supports vision, base64 images are passed through unchanged."""
    service = _make_service(enabled=True)

    async def run():
        # _resolve_image_urls will be called first — mock the session to pass through b64
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(side_effect=AssertionError("no HTTP calls expected"))
        with patch("lib.services.vision_service.aiohttp.ClientSession", return_value=mock_session):
            return await service.preprocess_messages(_make_messages_with_b64(), model_has_vision=True)

    result = asyncio.run(run())
    assert result[0].images == [FAKE_B64]


def test_model_lacks_vision_uses_fallback():
    """When model lacks vision, the fallback provider is called to describe images."""
    service = _make_service(enabled=True)

    mock_vision_provider = MagicMock()
    mock_vision_provider.chat = AsyncMock(return_value=ChatResponse(content="A dog playing fetch.", model="llama3.2-vision:11b"))

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(side_effect=AssertionError("no HTTP calls expected for b64"))

    async def run():
        with patch("lib.services.vision_service.aiohttp.ClientSession", return_value=mock_session):
            with patch("lib.services.vision_service.g_data") as mock_gd:
                mock_cfg = MagicMock()
                mock_cfg.data = {"providers": {"ollama": {}}}
                mock_gd.get = lambda key, default=None: mock_cfg if key == "cfg" else default
                with patch("lib.ai_providers.ProviderRegistry.get_provider", return_value=mock_vision_provider):
                    return await service.preprocess_messages(_make_messages_with_b64(), model_has_vision=False)

    result = asyncio.run(run())
    assert result[0].content and "A dog playing fetch" in result[0].content
    assert result[0].images is None


def test_no_images_returns_unchanged():
    """Messages without images pass through with zero processing."""
    service = _make_service()
    msgs = [Message(role="user", content="hello")]

    result = asyncio.run(service.preprocess_messages(msgs, model_has_vision=False))
    assert result is msgs


# ---------------------------------------------------------------------------
# _strip_images
# ---------------------------------------------------------------------------

def test_strip_images_removes_all():
    """_strip_images removes images from every message."""
    service = _make_service()
    msgs = [
        Message(role="user", content="pic1", images=["b64data"]),
        Message(role="assistant", content="reply", images=None),
        Message(role="user", content="pic2", images=["b64data2"]),
    ]
    result = service._strip_images(msgs)

    for m in result:
        assert m.images is None


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
