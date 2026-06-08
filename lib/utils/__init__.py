"""
Utilities module - shared utility functions.
"""
import re

from .dynamic_loader import DynamicLoader, ToolLoader, load_tools
from .project_root import resolve_project_root
from .tool_parser import extract_tool_from_xml


def slugify(text: str, max_length: int | None = None) -> str:
    """Convert text into a machine-friendly, filesystem-safe slug.

    Args:
        text: Input text to slugify.
        max_length: Optional maximum length. Truncates and strips trailing
                    dashes if exceeded. None means no limit.

    Returns:
        Lowercase slug with only [a-z0-9] and hyphens. Falls back to
        "unnamed" if the result would be empty.
    """
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    if max_length is not None and len(slug) > max_length:
        slug = slug[:max_length].rstrip('-')
    return slug or "unnamed"


def parse_model_string(model: str) -> tuple:
    """Parse model string in format <provider>/<model>.

    Args:
        model: Model string (e.g., "ollama/llama2", "copilot/gpt-4.1")

    Returns:
        Tuple of (provider_name, model_name)

    Raises:
        ValueError: If model format is invalid
    """
    if '/' not in model:
        raise ValueError(
            f"Invalid model format: '{model}'. "
            "Expected format: <provider>/<model> (e.g., 'ollama/llama2', 'copilot/gpt-4.1')"
        )
    parts = model.split('/', 1)
    return parts[0], parts[1]


def resolve_provider_model(
    model_str: str | None,
    fallback_provider: str = "ollama",
    fallback_model: str = "llama3.2",
) -> tuple[str, str]:
    """Resolve provider and model name from a possibly-prefixed model string.

    If ``model_str`` uses the ``<provider>/<model>`` format the provider is
    extracted from the prefix.  A bare model name (no slash) keeps the
    ``fallback_provider``.

    Args:
        model_str: Model string — either ``"provider/model"`` or bare ``"model"``.
        fallback_provider: Provider to use when no prefix is present.
        fallback_model: Model to use when ``model_str`` is None or empty.

    Returns:
        Tuple of (provider_name, model_name).
    """
    if model_str and '/' in model_str:
        return parse_model_string(model_str)
    return fallback_provider, model_str or fallback_model


__all__ = [
    "DynamicLoader",
    "ToolLoader",
    "load_tools",
    "extract_tool_from_xml",
    "parse_model_string",
    "resolve_provider_model",
    "resolve_project_root",
    "slugify",
]
