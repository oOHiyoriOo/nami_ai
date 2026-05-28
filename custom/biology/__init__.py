"""
Custom Biology Module — autonomous bio-state simulation for Nami AI.

Rewritten from the "shy lil sister" experimental codebase with:
- Corrected decay formulas (no double-scaling)
- Unified English action names
- SQLite persistence via scheduler.db
- Clean async/await patterns
"""
from custom.biology.engine import BiologyEngine
from custom.biology.needs import NeedsEngine
from custom.biology.consequences import ConsequenceTracker

__all__ = ["BiologyEngine", "NeedsEngine", "ConsequenceTracker"]
