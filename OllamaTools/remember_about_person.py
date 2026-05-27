"""
remember_about_person.py — Store a fact about a known person in Nami's memory graph.

Allows Nami to record third-party facts: "I met Sarah today", "Alice prefers dark mode",
"Bob works at ACME Corp" — linked to the person's graph node.
"""

import logging
import time
import uuid

from lib.global_registry import g_data
from lib.neo4j_lib.knowledge_unit import KnowledgeUnit
from OllamaTools import tool_error, tool_success


async def remember_about_person(person_name: str, fact: str) -> str:
    """
    Store a specific fact about a known person and link it to their PERSON node.

    Args:
        person_name: Name of the person this fact is about
        fact: The fact to remember (e.g. "works at ACME Corp", "prefers dark mode")
    """
    try:
        db = g_data.get("memory_db")
        if not db:
            return tool_error("Memory database not available")

        driver = db.get_driver()
        async with driver.session() as session:
            # Look up person by fuzzy regex match on name
            pattern = f"(?i).*{person_name}.*"
            result = await session.run(
                "MATCH (p:Person) WHERE p.name =~ $pattern RETURN p.id AS id, p.name AS name LIMIT 1",
                {"pattern": pattern}
            )
            record = await result.single()
            if not record:
                return tool_error(f"No person found matching '{person_name}'")

            person_id = record["id"]
            matched_name = record["name"]

            # Create KnowledgeUnit node
            k_id = str(uuid.uuid4())
            now = int(time.time() * 1000)  # epoch milliseconds, consistent with all other memory nodes
            ku = KnowledgeUnit(
                id=k_id,
                statement=fact,
                source="tool",
                type="fact_about_person",
                creationTimestamp=now,
            )

            # Generate embedding
            embedding = await db._encode(fact)
            ku.summaryEmbeddingVector = embedding

            ku_props = ku.get_properties()

            # Create the KnowledgeUnit and link to the Person
            query = """
            MATCH (p:Person {id: $person_id})
            CREATE (k:KnowledgeUnit $props)
            MERGE (k)-[:IS_ABOUT]->(p)
            RETURN k.id AS kid
            """
            await session.run(query, {"person_id": person_id, "props": ku_props})

            logging.info(
                f"remember_about_person: stored fact about '{matched_name}' "
                f"(id={person_id}): {fact[:60]}"
            )
            return tool_success({
                "fact_id": k_id,
                "person_id": person_id,
                "person_name": matched_name,
                "fact": fact,
                "action": "stored",
            })

    except Exception as e:
        logging.error(f"Error in remember_about_person: {e}")
        return tool_error(str(e))


def get_tool():
    """Return the remember_about_person tool schema."""
    return {
        "type": "function",
        "safe": False,
        "categories": ["memory_write"],
        "function": {
            "name": "remember_about_person",
            "description": "Store a specific fact about a known person and link it to their PERSON node. Use this to remember third-party information like job, preferences, or interactions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "person_name": {
                        "type": "string",
                        "description": "Name of the person this fact is about"
                    },
                    "fact": {
                        "type": "string",
                        "description": "The fact to remember (e.g. \"works at ACME Corp\", \"prefers dark mode\")"
                    }
                },
                "required": ["person_name", "fact"]
            }
        },
        "func": remember_about_person,
    }
