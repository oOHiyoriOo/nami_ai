"""
Neo4j memory type registry.

Provides centralized access to memory types for validation and introspection.
This eliminates the need for hardcoded whitelists throughout the codebase.
"""

from lib.neo4j_lib.episodic_memory import EpisodicMemory
from lib.neo4j_lib.knowledge_unit import KnowledgeUnit
from lib.neo4j_lib.procedural_unit import ProceduralUnit

# Registry mapping type name (Neo4j label) -> class
MEMORY_TYPE_CLASSES: dict[str, type] = {
    "EpisodicMemory": EpisodicMemory,
    "KnowledgeUnit": KnowledgeUnit,
    "ProceduralUnit": ProceduralUnit,
}

# Set of valid memory type names for quick validation
VALID_MEMORY_TYPES: frozenset[str] = frozenset(MEMORY_TYPE_CLASSES.keys())


def get_valid_properties(memory_type: str) -> set[str]:
    """
    Get valid property names for a memory type by inspecting its __init__ signature.
    
    Args:
        memory_type: Name of the memory type (e.g., "EpisodicMemory")
        
    Returns:
        Set of valid property names for that memory type
        
    Raises:
        ValueError: If memory_type is not valid
    """
    if memory_type not in MEMORY_TYPE_CLASSES:
        raise ValueError(f"Invalid memory_type: {memory_type}. Valid types: {VALID_MEMORY_TYPES}")
    
    cls = MEMORY_TYPE_CLASSES[memory_type]
    # Create a dummy instance to get property names from to_dict()
    # We use introspection of __init__ params instead for cleaner approach
    import inspect
    sig = inspect.signature(cls.__init__)
    # Exclude 'self' and **args/**kwargs
    return {
        param.name for param in sig.parameters.values()
        if param.name not in ('self', 'args', 'kwargs')
        and param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }


def is_valid_memory_type(memory_type: str) -> bool:
    """Check if a memory type name is valid."""
    return memory_type in VALID_MEMORY_TYPES


def is_valid_property(memory_type: str, property_name: str) -> bool:
    """
    Check if a property name is valid for a given memory type.
    
    Also allows common Neo4j metadata properties that may be added dynamically.
    """
    # Common properties that can be added to any memory type
    common_properties = {
        'lastModifiedTimestamp', 'lastAccessedTimestamp', 'access_count',
        'importance', 'mergedFromCount', 'decay_factor'
    }
    
    if property_name in common_properties:
        return True
    
    try:
        return property_name in get_valid_properties(memory_type)
    except ValueError:
        return False