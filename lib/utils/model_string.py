"""Utility for parsing model strings in <provider>/<model> format."""


def parse_model_string(model: str) -> tuple:
    """
    Parse model string in format <provider>/<model>.

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
