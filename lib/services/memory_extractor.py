"""
Memory Extractor - AI-powered memory extraction from messages.
Uses any AI provider to extract structured memories from conversation content.
"""
import logging
import asyncio
import json
import os
import re
from typing import Any
from dataclasses import dataclass
from datetime import datetime

from lib.ai_providers.base_provider import Message
from lib.chat_helper import format_user_message
from lib.global_registry import g_data
from lib.utils import slugify


@dataclass
class ExtractedMemory:
    """Represents a memory extracted by AI."""
    memory_type: str  # EpisodicMemory, KnowledgeUnit, ProceduralUnit, or "" (skip)
    memory_args: dict[str, Any]
    concepts: list[str]
    locations: list[dict[str, str]]

    def __init__(self, memory_type: str = "", memory_args: dict = None,
                 concepts: list = None, locations: list = None):
        self.memory_type = memory_type
        self.memory_args = memory_args or {}
        self.concepts = concepts or []
        self.locations = locations or []

    def is_valid(self) -> bool:
        """Check if this is a valid memory (not empty/skip)."""
        return self.memory_type in ("EpisodicMemory", "KnowledgeUnit", "ProceduralUnit")


_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "system_prompt", "memory")


def _load_prompt(filename: str) -> str:
    """Load a memory extraction prompt template by filename."""
    with open(os.path.join(_PROMPT_DIR, filename), encoding="utf-8") as f:
        return f.read()


def _get_memory_extraction_schema() -> dict:
    """
    Get JSON schema for memory extraction (Ollama structured output).
    
    Returns:
        JSON schema dict that enforces the memory extraction format
    """
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "memory_type": {
                    "type": "string",
                    "enum": ["EpisodicMemory", "KnowledgeUnit", "ProceduralUnit", ""]
                },
                "memory_args": {
                    "type": "object"
                },
                "concepts": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "locations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"}
                        },
                        "required": ["name"]
                    }
                }
            },
            "required": ["memory_type", "memory_args", "concepts"]
        }
    }


class MemoryExtractor:
    """
    Extracts structured memories from messages using AI.
    
    Platform-agnostic: works with any AI provider via ProviderRegistry.
    """

    def __init__(
        self,
        provider_registry,
        memory_db=None,
        provider_name: str = "ollama",
        model_name: str | None = None,
        max_retries: int = 3
    ):
        """
        Initialize memory extractor.

        Args:
            provider_registry: ProviderRegistry for AI access
            memory_db: Optional memory database for context retrieval
            provider_name: Which provider to use for extraction
            model_name: Model to use (if None, uses provider default)
            max_retries: Max retries on JSON parse failure
        """
        self.provider_registry = provider_registry
        self.memory_db = memory_db
        self.provider_name = provider_name
        self.model_name = model_name
        self.max_retries = max_retries
        self.extraction_prompt = _load_prompt("FACT_RETRIEVAL.md")
        self.short_extraction_prompt = _load_prompt("FACT_RETRIEVAL_SHORT.md")
        self.memory_schema = _get_memory_extraction_schema()

    async def extract_memories(
        self,
        message_content: str,
        user_name: str,
        timestamp: datetime | None = None
    ) -> list[ExtractedMemory]:
        """
        Extract memories from a message using AI.

        Args:
            message_content: The message text to analyze
            user_name: Author of the message
            timestamp: When the message was sent

        Returns:
            List of extracted memories (may be empty)
        """
        if not message_content or not message_content.strip():
            return []

        # Format message for extraction prompt (use shared formatting utility)
        ts = timestamp or datetime.now()
        timestamp_str = ts.strftime('%Y-%m-%d %H:%M:%S')
        formatted_message = format_user_message(user_name, timestamp_str, message_content)

        return await self._extract_with_retry(formatted_message)

    async def _extract_with_retry(
        self,
        formatted_message: str,
        attempt: int = 0
    ) -> list[ExtractedMemory]:
        """Extract with retry logic for JSON parse failures."""
        try:
            # Get relevant context from existing memories
            known_memories = await self._get_memory_context(formatted_message)
            
            # Resolve provider + config once (used for capabilities check AND the actual call)
            cfg = g_data.get('cfg')
            provider_config = cfg.data.get('providers', {}).get(self.provider_name, {})
            provider = self.provider_registry.get_provider(self.provider_name, provider_config)
            use_structured = provider.supports_structured_output()

            # Resolve model with proper fallback chain:
            # 1. Explicit model passed to constructor
            # 2. Memory extraction model from config
            # 3. Provider's default model
            model = self.model_name
            if not model:
                from lib.utils import resolve_provider_model
                memory_config = cfg.data.get('memory', {})
                _, model = resolve_provider_model(
                    memory_config.get('extraction_model'),
                    fallback_provider=self.provider_name,
                    fallback_model='',
                )
            if not model:
                model = provider_config.get('default_model')
            if not model:
                logging.warning("No extraction_model configured and provider has no default — falling back to 'llama3.2'")
                model = 'llama3.2'
            
            # Build messages for extraction (use appropriate prompt)
            prompt = self.short_extraction_prompt if use_structured else self.extraction_prompt
            messages = [
                {"role": "system", "content": prompt},
            ]
            
            if known_memories:
                messages.append({
                    "role": "user",
                    "content": f"[Existing memories for context]\n{known_memories}"
                })
            
            messages.append({"role": "user", "content": formatted_message})

            # Call AI provider (with or without structured output)
            raw_response = await self._call_provider(
                messages, provider, model,
                use_structured_output=use_structured
            )

            # Parse JSON response
            memories = self._parse_response(raw_response)
            
            if memories:
                logging.info(f"Extracted {len(memories)} memories from message (structured={use_structured})")
            
            return memories

        except json.JSONDecodeError as e:
            if attempt < self.max_retries:
                logging.warning(f"JSON parse failed (attempt {attempt + 1}), retrying...")
                await asyncio.sleep(0.5)
                return await self._extract_with_retry(formatted_message, attempt + 1)
            logging.error(f"Failed to parse AI response after {self.max_retries} retries: {e}")
            return []

        except Exception as e:
            logging.error(f"Memory extraction failed: {e}", exc_info=True)
            return []

    async def _call_provider(
        self, 
        messages: list[dict],
        provider,
        model: str,
        use_structured_output: bool = False
    ) -> str:
        """
        Call the AI provider and get response text.
        
        Args:
            messages: Messages to send to the AI
            provider: Pre-resolved provider instance
            model: Pre-resolved model name
            use_structured_output: Whether to use structured output (JSON schema enforcement)
        """
        mode = "structured" if use_structured_output else "prompt-based"
        logging.debug(f"Memory extraction using {self.provider_name}/{model} ({mode})")

        # Convert to provider message format
        provider_messages = [
            Message(role=m['role'], content=m['content'])
            for m in messages
        ]

        # Build kwargs
        call_kwargs = {'model': model}
        
        # Add format schema if using structured output
        if use_structured_output:
            call_kwargs['format'] = self.memory_schema

        # Call provider (async)
        response = await provider.chat(
            messages=provider_messages,
            **call_kwargs
        )

        return response.content

    async def _get_memory_context(self, query: str) -> str:
        """Get relevant existing memories as context."""
        if not self.memory_db:
            return ""

        try:
            results = await self.memory_db.search(query, top_k=5)
            if not results:
                return ""

            structured = [
                {"memory": str(result[0]), "similarity": str(result[1])}
                for result in results
            ]
            return json.dumps(structured)

        except Exception as e:
            logging.warning(f"Failed to get memory context: {e}")
            return ""

    def _parse_response(self, raw_response: str) -> list[ExtractedMemory]:
        """Parse AI response into ExtractedMemory objects."""
        # Clean response - remove think blocks, strip surrounding whitespace
        # and code fences, but preserve newlines inside JSON string values
        cleaned = re.sub(r'<think>.*?</think>', '', raw_response, flags=re.DOTALL)
        cleaned = cleaned.strip()
        cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```\s*$', '', cleaned)

        parsed = json.loads(cleaned)

        if not isinstance(parsed, list):
            logging.warning(f"AI response was not a list: {cleaned[:100]}")
            return []

        memories = []
        for item in parsed:
            memory = ExtractedMemory(
                memory_type=item.get('memory_type', ''),
                memory_args=item.get('memory_args', {}),
                concepts=item.get('concepts', []),
                locations=item.get('locations', []),
            )
            if memory.is_valid():
                memories.append(memory)

        return memories
