"""project_root.py — Canonical project-root resolver used across services and tools.

Previously this function was copy-pasted into three different files.
Now it lives here so that any change to the anchor-path logic only
needs to happen in one place.
"""

from pathlib import Path


def resolve_project_root() -> Path:
    """Find the project root containing api_server.py.

    Tries well-known anchor paths first, then walks upward from cwd.
    """
    for anchor in [Path("/workspace/project/nami_ai"), Path.cwd()]:
        if (anchor / "api_server.py").exists():
            return anchor
    current = Path.cwd()
    for _ in range(5):
        if (current / "api_server.py").exists():
            return current
        current = current.parent
    return Path.cwd()
