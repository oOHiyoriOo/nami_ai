"""
Vision processing service for handling images in conversations.

Provides image analysis fallback for models without native vision support.
"""
import asyncio
import base64
import logging
from typing import Any

import aiohttp

from lib.ai_providers import Message, ChatResponse
from lib.global_registry import g_data
from lib.utils.url_utils import is_safe_url as _is_safe_url


class VisionService:
    """
    Handles vision preprocessing for chat messages.
    
    Automatically analyzes images using a vision model when the target chat model
    doesn't support vision, injecting descriptions into message content.
    """
    
    def __init__(self, config: dict[str, Any]):
        """
        Initialize vision service.
        
        Args:
            config: Vision configuration dict with:
                - enabled: bool - Master switch
                - fallback_provider: str - Provider for vision analysis
                - fallback_model: str - Vision model name
                - max_image_size: int - Max image size in bytes
        """
        self.enabled = config.get('enabled', True)
        self.fallback_provider = config.get('fallback_provider', 'ollama')
        self.fallback_model = config.get('fallback_model', 'llama3.2-vision:11b')
        self.max_image_size = config.get('max_image_size', 5242880)  # 5MB default
        
        logging.info(
            f"VisionService initialized - enabled: {self.enabled}, "
            f"fallback: {self.fallback_provider}/{self.fallback_model}"
        )
    
    async def preprocess_messages(
        self,
        messages: list[Message],
        model_has_vision: bool
    ) -> list[Message]:
        """
        Preprocess messages based on vision capabilities.
        
        Logic:
        - If vision disabled: Strip all images, log warning
        - If model has vision: Pass through unchanged
        - If model lacks vision: Analyze images → inject descriptions → strip images
        
        Args:
            messages: List of messages (may contain images)
            model_has_vision: Whether target model supports vision
        
        Returns:
            Preprocessed messages ready for chat model
        """
        # Check if any message has images
        has_images = any(msg.images for msg in messages)
        
        if not has_images:
            return messages  # No images, no preprocessing needed

        # Download any HTTP/S image URLs to base64 so every downstream model
        # (Ollama native vision or fallback) receives raw bytes, not CDN URLs.
        messages = await self._resolve_image_urls(messages)
        
        if not self.enabled:
            logging.warning("Vision disabled - stripping images from messages")
            return self._strip_images(messages)
        
        if model_has_vision:
            logging.debug("Model supports vision - passing images through")
            return messages  # Model can handle images natively
        
        # Model doesn't support vision - use fallback
        logging.info(f"Model lacks vision - using {self.fallback_provider}/{self.fallback_model} for analysis")
        return await self._analyze_and_inject(messages)
    
    async def _analyze_and_inject(self, messages: list[Message]) -> list[Message]:
        """
        Analyze images using vision model and inject descriptions into content.
        
        Args:
            messages: Messages with images
        
        Returns:
            Messages with image descriptions injected, images stripped
        """
        from lib.ai_providers import ProviderRegistry

        # Get vision provider using the same registry as the rest of the app
        try:
            cfg = g_data.get("cfg")
            provider_config = cfg.data.get("providers", {}).get(self.fallback_provider, {}) if cfg else {}
            vision_provider = ProviderRegistry.get_provider(self.fallback_provider, provider_config)
        except Exception as e:
            logging.error(f"Failed to load vision provider '{self.fallback_provider}': {e}")
            logging.warning("Stripping images without analysis")
            return self._strip_images(messages)
        
        processed = []
        for msg in messages:
            if not msg.images:
                processed.append(msg)
                continue
            
            # Analyze images
            try:
                descriptions = await self._analyze_images(vision_provider, msg.images, msg.content)
                
                # Inject descriptions into content
                enhanced_content = f"{msg.content}\n\n[Image Analysis]\n{descriptions}"
                
                processed.append(Message(
                    role=msg.role,
                    content=enhanced_content,
                    name=msg.name,
                    tool_calls=msg.tool_calls,
                    images=None,  # Strip images after analysis
                    tool_call_id=msg.tool_call_id,
                ))
                
            except Exception as e:
                logging.error(f"Image analysis failed: {e}")
                # Keep original message without images
                processed.append(Message(
                    role=msg.role,
                    content=msg.content,
                    name=msg.name,
                    tool_calls=msg.tool_calls,
                    images=None,
                    tool_call_id=msg.tool_call_id,
                ))
        
        return processed
    
    async def _analyze_images(
        self,
        vision_provider: Any,
        images: list[str],
        user_message: str
    ) -> str:
        """
        Call vision model to analyze images.
        
        Args:
            vision_provider: AI provider instance with vision support
            images: List of base64-encoded images
            user_message: Original user message for context
        
        Returns:
            Concatenated descriptions of all images
        """
        vision_messages = [
            Message(
                role="user",
                content=(
                    f"Analyze these images in the context of the user's message: \"{user_message}\"\n\n"
                    "Provide a concise description of what you see. Focus on details relevant to the user's question."
                ),
                images=images
            )
        ]
        
        response: ChatResponse = await vision_provider.chat(
            messages=vision_messages,
            model=self.fallback_model
        )
        
        return response.content.strip()
    
    async def _resolve_image_urls(self, messages: list[Message]) -> list[Message]:
        """
        Download any HTTP/S image URLs in messages and convert them to base64.

        Ollama (and most local providers) require images as raw base64 strings,
        not CDN URLs. Discord, WhatsApp, and other platforms supply URLs, so we
        fetch them here before any model sees the messages.

        Images that are already base64 (no ``://`` in the string) are left as-is.

        Args:
            messages: Messages that may contain URL or base64 image strings.

        Returns:
            Messages with all image entries as base64 strings.
        """
        resolved = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
            for msg in messages:
                if not msg.images:
                    resolved.append(msg)
                    continue

                b64_images = []
                for img in msg.images:
                    if img.startswith("http://") or img.startswith("https://"):
                        loop = asyncio.get_running_loop()
                        if not await loop.run_in_executor(None, _is_safe_url, img):
                            logging.warning(f"[vision] Blocked unsafe image URL: {img}")
                            continue
                        try:
                            async with client.get(img) as resp:
                                resp.raise_for_status()
                                data = await resp.read()
                                b64_images.append(base64.b64encode(data).decode())
                                logging.debug(f"[vision] Downloaded image from URL ({len(data)} bytes)")
                        except Exception as e:
                            logging.warning(f"[vision] Failed to download image URL: {e}")
                    else:
                        b64_images.append(img)  # already base64

                resolved.append(Message(
                    role=msg.role,
                    content=msg.content,
                    name=msg.name,
                    tool_calls=msg.tool_calls,
                    images=b64_images or None,
                    tool_call_id=msg.tool_call_id,
                ))
        return resolved

    def _strip_images(self, messages: list[Message]) -> list[Message]:
        """
        Remove images from all messages.
        
        Args:
            messages: Messages potentially containing images
        
        Returns:
            Messages with images field set to None
        """
        return [
            Message(
                role=msg.role,
                content=msg.content,
                name=msg.name,
                tool_calls=msg.tool_calls,
                images=None,
                tool_call_id=msg.tool_call_id,
            )
            for msg in messages
        ]
