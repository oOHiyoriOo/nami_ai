"""
Model Cache Service
Tracks successfully used models and provides quick access to them.
"""
import json
import logging
import os
from datetime import datetime
from typing import Any
from dataclasses import dataclass, asdict

CACHE_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model_cache.json"))


@dataclass
class CachedModel:
    """Represents a cached model with usage metadata."""
    name: str  # Full name: provider/model
    provider: str
    model: str
    first_used: str  # ISO format timestamp
    last_used: str  # ISO format timestamp
    success_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary format."""
        return asdict(self)


class ModelCache:
    """
    Cache for tracking successfully used models.

    This service maintains a list of models that have been successfully used
    in the system, providing quick access to validated models.
    """

    def __init__(self):
        """Initialize the model cache."""
        self._cache: dict[str, CachedModel] = {}
        self.logger = logging.getLogger(__name__)
        self._load_from_disk()

    def record_success(self, full_model_name: str) -> None:
        """
        Record a successful model usage.

        Args:
            full_model_name: Full model name in format "provider/model"
        """
        try:
            # Parse model name
            if '/' not in full_model_name:
                self.logger.warning(f"Invalid model format: {full_model_name}")
                return

            provider, model = full_model_name.split('/', 1)
            current_time = datetime.utcnow().isoformat() + "Z"

            if full_model_name in self._cache:
                # Update existing entry
                cached = self._cache[full_model_name]
                cached.last_used = current_time
                cached.success_count += 1
                self.logger.debug(f"Updated cache for model: {full_model_name} (count: {cached.success_count})")
            else:
                # Create new entry
                self._cache[full_model_name] = CachedModel(
                    name=full_model_name,
                    provider=provider,
                    model=model,
                    first_used=current_time,
                    last_used=current_time,
                    success_count=1
                )
                self.logger.info(f"Added new model to cache: {full_model_name}")

        except Exception as e:
            self.logger.error(f"Error recording model success: {e}", exc_info=True)
        else:
            self._save_to_disk()

    def get_cached_models(self, provider: str | None = None) -> list[CachedModel]:
        """
        Get all cached models, optionally filtered by provider.

        Args:
            provider: Optional provider name to filter by

        Returns:
            List of cached models, sorted by last used (most recent first)
        """
        models = list(self._cache.values())

        # Filter by provider if specified
        if provider:
            models = [m for m in models if m.provider == provider]

        # Sort by last used, most recent first
        models.sort(key=lambda m: m.last_used, reverse=True)

        return models

    def get_model(self, full_model_name: str) -> CachedModel | None:
        """
        Get a specific cached model.

        Args:
            full_model_name: Full model name in format "provider/model"

        Returns:
            CachedModel if found, None otherwise
        """
        return self._cache.get(full_model_name)

    def is_cached(self, full_model_name: str) -> bool:
        """
        Check if a model is in the cache.

        Args:
            full_model_name: Full model name in format "provider/model"

        Returns:
            True if model is cached
        """
        return full_model_name in self._cache

    def get_cache_stats(self) -> dict[str, Any]:
        """
        Get statistics about the cache.

        Returns:
            Dictionary with cache statistics
        """
        total_models = len(self._cache)
        providers = set(m.provider for m in self._cache.values())
        total_successes = sum(m.success_count for m in self._cache.values())

        # Most used model
        most_used = None
        if self._cache:
            most_used_model = max(self._cache.values(), key=lambda m: m.success_count)
            most_used = {
                "name": most_used_model.name,
                "count": most_used_model.success_count
            }

        return {
            "total_models": total_models,
            "providers": list(providers),
            "total_successes": total_successes,
            "most_used": most_used
        }

    def clear(self) -> None:
        """Clear all cached models."""
        self._cache.clear()
        self._save_to_disk()
        self.logger.info("Model cache cleared")

    def _save_to_disk(self) -> None:
        """Persist the current cache to a JSON file."""
        try:
            data = {k: asdict(v) for k, v in self._cache.items()}
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError as e:
            self.logger.error(f"Failed to save model cache: {e}")

    def _load_from_disk(self) -> None:
        """Load cached models from a JSON file."""
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self._cache = {k: CachedModel(**v) for k, v in data.items()}
            self.logger.info(f"Loaded {len(self._cache)} cached models from disk")
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"Failed to load model cache: {e}")

    def to_ollama_format(self, provider: str | None = None) -> list[dict[str, Any]]:
        """
        Convert cached models to Ollama-compatible format.

        Args:
            provider: Optional provider name to filter by

        Returns:
            List of models in Ollama format
        """
        models = self.get_cached_models(provider)

        return [
            {
                "name": model.name,
                "modified_at": model.last_used,
                "size": 0,
                "digest": "",
                "details": {
                    "provider": model.provider,
                    "model": model.model,
                    "success_count": model.success_count,
                    "first_used": model.first_used,
                    "last_used": model.last_used
                }
            }
            for model in models
        ]
