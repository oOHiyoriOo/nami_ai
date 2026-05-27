def format_user_message(name: str, timestamp: str, content: str) -> str:
    """
    Format a user message with name and timestamp.

    Standard format: "{name} [{timestamp}] : {content}"

    Args:
        name: Display name of the user
        timestamp: Timestamp string (e.g., "2026-02-12 14:30:00")
        content: Message content

    Returns:
        Formatted message string
    """
    return f"{name} [{timestamp}] : {content}"
