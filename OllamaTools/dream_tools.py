"""
dream_tools.py — Memory management tools exclusively for the Auto-Dream agent.

These tools are NOT loaded during normal startup (dynamic_loader skips files
prefixed with 'dream_'). They are injected only when the DreamService spawns
the dream agent.

The dream agent uses these to read, search, update, merge, delete, and promote
memories during its consolidation pass over Nami's memory graph.
"""

import json
import logging
from lib.global_registry import g_data


def _memory_db():
    return g_data.get("memory_db")


def _format_memory(mem_obj) -> dict:
    """Convert a memory object to a clean dict for the AI."""
    d = mem_obj.to_dict() if hasattr(mem_obj, "to_dict") else vars(mem_obj)
    # Strip the embedding vector — huge and useless for the AI to read
    d.pop("summaryEmbeddingVector", None)
    return d


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def dream_list_memories(
    memory_type: str = "all",
    limit: int = 20,
) -> str:
    """
    List memories from the graph, newest first.

    Args:
        memory_type: "EpisodicMemory", "KnowledgeUnit", "ProceduralUnit", or "all"
        limit:       Max number of memories to return (default 20)

    Returns:
        JSON list of memory dicts with id, type, and content fields.
    """
    db = _memory_db()
    if not db:
        return "Error: memory_db not available"

    valid_types = list(db.MEMORY_TYPES.keys())
    if memory_type == "all":
        labels = valid_types
    elif memory_type in valid_types:
        labels = [memory_type]
    else:
        return f"Error: unknown memory_type '{memory_type}'. Valid: {valid_types + ['all']}"

    results = []
    try:
        driver = db.get_driver()
        async with driver.session() as session:
            for label in labels:
                cypher = f"""
                MATCH (m:{label})
                RETURN m, labels(m)[0] AS type
                ORDER BY m.creationTimestamp DESC
                LIMIT $limit
                """
                res = await session.run(cypher, {"limit": limit})
                async for record in res:
                    mem = db._node_to_memory_object(record["m"], label)
                    d = _format_memory(mem)
                    d["memory_type"] = label
                    results.append(d)
    except Exception as e:
        return f"Error listing memories: {e}"

    return json.dumps(results, default=str, indent=2)


async def dream_search_memories(query: str, limit: int = 10) -> str:
    """
    Search all memories by semantic similarity to the query.

    Args:
        query: Natural language search query
        limit: Max results to return (default 10)

    Returns:
        JSON list of {score, memory_type, memory} dicts ordered by relevance.
    """
    db = _memory_db()
    if not db:
        return "Error: memory_db not available"

    try:
        results = await db.search(query=query, top_k=limit)
        output = []
        for mem_obj, score in results:
            d = _format_memory(mem_obj)
            output.append({
                "score": round(score, 4),
                "memory_type": type(mem_obj).__name__,
                "memory": d,
            })
        return json.dumps(output, default=str, indent=2)
    except Exception as e:
        return f"Error searching memories: {e}"


async def dream_get_memory(memory_id: str, memory_type: str) -> str:
    """
    Fetch a single memory by its ID in full detail.

    Args:
        memory_id:   The UUID of the memory node.
        memory_type: "EpisodicMemory", "KnowledgeUnit", or "ProceduralUnit"

    Returns:
        JSON dict of the memory's full properties.
    """
    db = _memory_db()
    if not db:
        return "Error: memory_db not available"

    if memory_type not in db.MEMORY_TYPES:
        return f"Error: invalid memory_type '{memory_type}'. Valid: {list(db.MEMORY_TYPES.keys())}"

    try:
        driver = db.get_driver()
        async with driver.session() as session:
            cypher = f"MATCH (m:{memory_type} {{id: $id}}) RETURN m LIMIT 1"
            res = await session.run(cypher, {"id": memory_id})
            record = await res.single()
            if not record:
                return f"No {memory_type} found with id={memory_id}"
            mem = db._node_to_memory_object(record["m"], memory_type)
            d = _format_memory(mem)
            d["memory_type"] = memory_type
            return json.dumps(d, default=str, indent=2)
    except Exception as e:
        return f"Error fetching memory {memory_id}: {e}"


async def dream_update_memory(
    memory_id: str,
    memory_type: str,
    new_content: str,
) -> str:
    """
    Rewrite the main text content of a memory node.

    Use this to fix stale phrasing, resolve contradictions, or add precision.
    Only updates the primary text field (summary/statement/description).

    Args:
        memory_id:   UUID of the memory to update.
        memory_type: "EpisodicMemory", "KnowledgeUnit", or "ProceduralUnit"
        new_content: Replacement text for the primary content field.

    Returns:
        Confirmation string.
    """
    db = _memory_db()
    if not db:
        return "Error: memory_db not available"

    # Primary text field per type
    field_map = {
        "EpisodicMemory": "summary",
        "KnowledgeUnit": "statement",
        "ProceduralUnit": "description",
    }
    if memory_type not in field_map:
        return f"Error: invalid memory_type '{memory_type}'"

    field = field_map[memory_type]

    try:
        # Re-encode the embedding for the new content
        new_embedding = await db._encode(new_content)
        driver = db.get_driver()
        async with driver.session() as session:
            cypher = f"""
            MATCH (m:{memory_type} {{id: $id}})
            SET m.{field} = $content, m.summaryEmbeddingVector = $embedding
            RETURN m.id AS updated_id
            """
            res = await session.run(cypher, {
                "id": memory_id,
                "content": new_content,
                "embedding": new_embedding,
            })
            record = await res.single()
            if not record:
                return f"No {memory_type} found with id={memory_id}"
        logging.info(f"[dream] Updated {memory_type} {memory_id}")
        return f"Updated {memory_type} {memory_id}: {field} = '{new_content[:80]}...'"
    except Exception as e:
        return f"Error updating memory {memory_id}: {e}"


async def dream_delete_memory(memory_id: str, memory_type: str, reason: str = "") -> str:
    """
    Permanently delete a memory node and all its relationships.

    Use this for memories that are: contradicted, duplicate, or clearly stale.
    Always state a reason — it helps the report at the end.

    Args:
        memory_id:   UUID of the memory to delete.
        memory_type: "EpisodicMemory", "KnowledgeUnit", or "ProceduralUnit"
        reason:      Why this memory is being removed.

    Returns:
        Confirmation string.
    """
    db = _memory_db()
    if not db:
        return "Error: memory_db not available"

    if memory_type not in db.MEMORY_TYPES:
        return f"Error: invalid memory_type '{memory_type}'"

    try:
        driver = db.get_driver()
        async with driver.session() as session:
            cypher = f"MATCH (m:{memory_type} {{id: $id}}) DETACH DELETE m RETURN count(m) AS deleted"
            res = await session.run(cypher, {"id": memory_id})
            record = await res.single()
            count = record["deleted"] if record else 0
        if count:
            logging.info(f"[dream] Deleted {memory_type} {memory_id} — reason: {reason}")
            return f"Deleted {memory_type} {memory_id}. Reason: {reason}"
        return f"No {memory_type} found with id={memory_id} (may already be gone)"
    except Exception as e:
        return f"Error deleting memory {memory_id}: {e}"


async def dream_merge_memories(
    keep_id: str,
    keep_type: str,
    delete_id: str,
    delete_type: str,
    merged_content: str,
) -> str:
    """
    Merge two near-duplicate memories into one.

    Updates the kept memory with merged_content, then deletes the other.
    Relationships from the deleted memory are NOT transferred (acceptable trade-off).

    Args:
        keep_id:        UUID of the memory to keep and update.
        keep_type:      Type of the memory to keep.
        delete_id:      UUID of the duplicate to remove.
        delete_type:    Type of the duplicate.
        merged_content: New combined text for the kept memory.

    Returns:
        Confirmation string.
    """
    update_result = await dream_update_memory(keep_id, keep_type, merged_content)
    if update_result.startswith("Error"):
        return update_result
    delete_result = await dream_delete_memory(delete_id, delete_type, reason=f"merged into {keep_id}")
    logging.info(f"[dream] Merged {delete_type} {delete_id} → {keep_type} {keep_id}")
    return f"Merged: {update_result} | {delete_result}"


async def dream_get_stats() -> str:
    """
    Return memory graph stats: total memories per type.

    Use this at the start of a dream to orient quickly.

    Returns:
        JSON dict with counts per memory type and total.
    """
    db = _memory_db()
    if not db:
        return "Error: memory_db not available"

    try:
        stats = {}
        total = 0
        driver = db.get_driver()
        async with driver.session() as session:
            for label in db.MEMORY_TYPES:
                res = await session.run(f"MATCH (m:{label}) RETURN count(m) AS n")
                record = await res.single()
                count = record["n"] if record else 0
                stats[label] = count
                total += count
        stats["total"] = total
        return json.dumps(stats)
    except Exception as e:
        return f"Error getting stats: {e}"


# ---------------------------------------------------------------------------
# Tool registry — returned as a list (same pattern as schedule_task.py)
# ---------------------------------------------------------------------------

def get_tool() -> list[dict]:
    """
    Return all dream-agent tools as a list.

    These are injected by DreamService and never loaded by the normal
    ToolLoader (which skips files starting with 'dream_').
    """
    return [
        {
            "type": "function",
            "safe": True,
            "categories": ["memory_read"],
            "function": {
                "name": "dream_get_stats",
                "description": "Get memory graph stats (counts per type). Use first to orient.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            "func": dream_get_stats,
        },
        {
            "type": "function",
            "safe": True,
            "categories": ["memory_read"],
            "function": {
                "name": "dream_list_memories",
                "description": "List memories newest-first, optionally filtered by type.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_type": {
                            "type": "string",
                            "description": "'EpisodicMemory', 'KnowledgeUnit', 'ProceduralUnit', or 'all'",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 20)"},
                    },
                    "required": [],
                },
            },
            "func": dream_list_memories,
        },
        {
            "type": "function",
            "safe": True,
            "categories": ["memory_read"],
            "function": {
                "name": "dream_search_memories",
                "description": "Search memories by semantic similarity to a query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural language search query"},
                        "limit": {"type": "integer", "description": "Max results (default 10)"},
                    },
                    "required": ["query"],
                },
            },
            "func": dream_search_memories,
        },
        {
            "type": "function",
            "safe": True,
            "categories": ["memory_read"],
            "function": {
                "name": "dream_get_memory",
                "description": "Fetch a single memory in full detail by its UUID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "UUID of the memory node"},
                        "memory_type": {
                            "type": "string",
                            "description": "'EpisodicMemory', 'KnowledgeUnit', or 'ProceduralUnit'",
                        },
                    },
                    "required": ["memory_id", "memory_type"],
                },
            },
            "func": dream_get_memory,
        },
        {
            "type": "function",
            "safe": False,
            "categories": ["memory_write"],
            "function": {
                "name": "dream_update_memory",
                "description": "Rewrite the content of a memory (fixes stale phrasing, contradictions).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "memory_type": {"type": "string"},
                        "new_content": {"type": "string", "description": "New text for the primary field"},
                    },
                    "required": ["memory_id", "memory_type", "new_content"],
                },
            },
            "func": dream_update_memory,
        },
        {
            "type": "function",
            "safe": False,
            "categories": ["memory_write"],
            "function": {
                "name": "dream_delete_memory",
                "description": "Permanently delete a stale, wrong, or duplicate memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "memory_type": {"type": "string"},
                        "reason": {"type": "string", "description": "Why this memory is being removed"},
                    },
                    "required": ["memory_id", "memory_type"],
                },
            },
            "func": dream_delete_memory,
        },
        {
            "type": "function",
            "safe": False,
            "categories": ["memory_write"],
            "function": {
                "name": "dream_merge_memories",
                "description": "Merge two duplicate memories: update one with combined content, delete the other.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keep_id": {"type": "string", "description": "UUID of memory to keep"},
                        "keep_type": {"type": "string"},
                        "delete_id": {"type": "string", "description": "UUID of duplicate to remove"},
                        "delete_type": {"type": "string"},
                        "merged_content": {"type": "string", "description": "Combined text for the kept memory"},
                    },
                    "required": ["keep_id", "keep_type", "delete_id", "delete_type", "merged_content"],
                },
            },
            "func": dream_merge_memories,
        },
    ]
