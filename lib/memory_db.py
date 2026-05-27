from neo4j import AsyncGraphDatabase, NotificationMinimumSeverity
from ollama import AsyncClient as OllamaAsyncClient
from typing import Any
import logging
import uuid

from lib.neo4j_lib.episodic_memory import EpisodicMemory
from lib.neo4j_lib.knowledge_unit import KnowledgeUnit
from lib.neo4j_lib.procedural_unit import ProceduralUnit
from lib.neo4j_lib.person import Person
from lib.neo4j_lib.concept import Concept

class MemoryDb:
    """Central interface to the Neo4j-backed persistent memory store.

    Manages memory type registrations and provides search, store, and
    retrieve operations for episodic, knowledge, and procedural memories.
    Uses Ollama embeddings for vector similarity search across all memory types.
    """

    # Memory type configurations: (label, class, text_field)
    MEMORY_TYPES = {
        "EpisodicMemory": (EpisodicMemory, 'summary'),
        "KnowledgeUnit": (KnowledgeUnit, 'statement'),
        "ProceduralUnit": (ProceduralUnit, 'description'),
    }

    @classmethod
    def get_text_field(cls, memory_type: str) -> str | None:
        """Return the canonical text field name for a memory type, or None if unknown."""
        entry = cls.MEMORY_TYPES.get(memory_type)
        return entry[1] if entry else None

    def __init__(self, neo4j_uri: str, neo4j_user: str, neo4j_pass: str,
                 embedding_model: str = 'nomic-embed-text',
                 embedding_dimension: int = 768,
                 embedding_max_input_chars: int = 6000,
                 ollama_url: str = 'http://localhost:11434'):
        """
        Initializes the Memory Database connection using Neo4j and Ollama embeddings.

        Args:
            neo4j_uri (str): URI for the Neo4j database.
            neo4j_user (str): Username for Neo4j connection.
            neo4j_pass (str): Password for Neo4j connection.
            embedding_model (str): Ollama model name for generating embeddings.
            embedding_dimension (int): Vector dimension of the embedding model.
            embedding_max_input_chars (int): Max characters sent to embedding model.
            ollama_url (str): Base URL of the Ollama instance.
        """
        self.embedding_model = embedding_model
        self.dimension = embedding_dimension
        self.embedding_max_input_chars = max(512, int(embedding_max_input_chars))
        self._ollama = OllamaAsyncClient(host=ollama_url)
        logging.info(
            f"Ollama embedding model '{embedding_model}' (dim={embedding_dimension}, "
            f"max_input_chars={self.embedding_max_input_chars}) via {ollama_url}."
        )

        self.neo4j_uri = neo4j_uri
        self.neo4j_auth = (neo4j_user, neo4j_pass)
        self._driver = None
        self._shutting_down = False
        logging.info(f"Neo4j async driver configured for URI: {self.neo4j_uri}")
        # Note: setup_indices() must be called separately from async context
        logging.info(f"MemoryDb initialized for Neo4j (call setup_indices() from async context).")

    def _shorten_for_embedding(self, text: str, max_chars: int) -> str:
        """Trim overly long embedding input while preserving the start and end context."""
        if len(text) <= max_chars:
            return text

        marker = "\n...[truncated for embedding]...\n"
        if max_chars <= len(marker) + 32:
            return text[:max_chars]

        head = int(max_chars * 0.8)
        tail = max_chars - head - len(marker)
        if tail < 16:
            tail = 16
            head = max_chars - len(marker) - tail

        return f"{text[:head]}{marker}{text[-tail:]}"

    async def _encode(self, text: str) -> list[float]:
        """Generate an embedding vector for the given text via Ollama.

        Args:
            text: The text to embed.

        Returns:
            Flat list of floats representing the embedding vector.
        """
        raw_text = text or ""
        limits = [
            self.embedding_max_input_chars,
            max(self.embedding_max_input_chars // 2, 1024),
            512,
        ]

        # Keep unique limits while preserving order.
        unique_limits = list(dict.fromkeys(limits))
        last_error = None

        for max_chars in unique_limits:
            candidate = self._shorten_for_embedding(raw_text, max_chars)
            if len(raw_text) > max_chars:
                logging.debug(
                    "Truncated embedding input from %s to %s chars for model '%s'.",
                    len(raw_text),
                    len(candidate),
                    self.embedding_model,
                )

            try:
                response = await self._ollama.embed(model=self.embedding_model, input=candidate)
                return response.embeddings[0]
            except Exception as e:
                last_error = e
                msg = str(e).lower()
                if "context length" in msg and max_chars != unique_limits[-1]:
                    logging.warning(
                        "Embedding input exceeded model context at %s chars; retrying with shorter input.",
                        max_chars,
                    )
                    continue
                raise

        # Defensive fallback: this line should rarely be reached.
        if last_error:
            raise last_error
        raise RuntimeError("Failed to generate embedding: no retry attempts executed")

    def get_driver(self):
        """Ensures an active async driver exists."""
        if self._shutting_down:
            raise RuntimeError("MemoryDb is shutting down — cannot create new driver sessions")
        if self._driver is None or self._driver._closed:
            self._driver = AsyncGraphDatabase.driver(
                self.neo4j_uri,
                auth=self.neo4j_auth,
                # Suppress schema-level notifications (e.g. "relationship type does not exist")
                # that fire harmlessly when querying relationships not yet created.
                notifications_min_severity=NotificationMinimumSeverity.OFF,
            )
            logging.info("Neo4j async driver created.")
        return self._driver

    async def close(self):
        """Closes the Neo4j async driver connection safely."""
        if self._shutting_down:
            return
        self._shutting_down = True
        if self._driver is not None and not self._driver._closed:
            await self._driver.close()
            logging.info("Neo4j async driver closed.")

    async def add_person(self, user_id: str, name: str, description: str = "", metadata: dict = {}) -> None:
        """Create or update a Person node in the memory graph."""
        driver = self.get_driver()
        async with driver.session() as session:
            props = {"name": name, "description": description, **metadata}
            query = """
            MERGE (p:Person {id: $user_id})
            SET p += $props
            """
            await session.run(query, {"user_id": user_id, "props": props})
            logging.debug(f"Person upserted: {user_id} ({name})")

    async def add_location(self, location_id: str, name: str, description: str = "", metadata: dict = {}) -> None:
        """Create or update a Location node in the memory graph."""
        driver = self.get_driver()
        async with driver.session() as session:
            props = {"name": name, "description": description, **metadata}
            query = """
            MERGE (l:Location {location_id: $location_id})
            SET l += $props
            """
            await session.run(query, {"location_id": location_id, "props": props})
            logging.debug(f"Location upserted: {location_id} ({name})")

    async def link_person_identities(self, user_id_a: str, user_id_b: str, linked_by: str = "") -> None:
        """Create a :SAME_PERSON_AS relationship between two Person nodes.

        Links two platform-scoped identities so Nami recognises them as the
        same human.  The relationship is undirected and carries provenance.

        Args:
            user_id_a: Scoped user ID (e.g. 'discord:123').
            user_id_b: Scoped user ID (e.g. 'whatsapp:456').
            linked_by: Who or what created the link (e.g. 'user_request',
                       'admin').  Empty string = unspecified.
        """
        from datetime import datetime, timezone
        driver = self.get_driver()
        async with driver.session() as session:
            query = """
            MATCH (a:Person {id: $user_id_a}), (b:Person {id: $user_id_b})
            MERGE (a)-[:SAME_PERSON_AS {linked_by: $linked_by, linked_at: $linked_at}]-(b)
            """
            await session.run(query, {
                "user_id_a": user_id_a,
                "user_id_b": user_id_b,
                "linked_by": linked_by,
                "linked_at": datetime.now(timezone.utc).isoformat(),
            })
            logging.info(f"Linked identities: {user_id_a} ↔ {user_id_b} (by: {linked_by or 'unspecified'})")

    async def resolve_canonical_users(self, user_id: str) -> list[str]:
        """Return all user_ids linked to this Person via :SAME_PERSON_AS.

        Traverses undirected SAME_PERSON_AS relationships to find every
        identity that belongs to the same human.  The result always includes
        the input user_id itself (zero-hop traversal).

        Args:
            user_id: Scoped user ID (e.g. 'discord:123').

        Returns:
            List of user_id strings including the input.
        """
        driver = self.get_driver()
        async with driver.session() as session:
            query = """
            MATCH (p:Person {id: $user_id})-[:SAME_PERSON_AS*0..]-(linked:Person)
            RETURN DISTINCT linked.id AS user_id
            ORDER BY user_id
            """
            result = await session.run(query, {"user_id": user_id})
            records = [record async for record in result]
            return [r["user_id"] for r in records]

    def _validate_memory_type(self, memory_type: str) -> None:
        """Validates that memory_type is in the whitelist to prevent injection attacks."""
        if memory_type not in self.MEMORY_TYPES:
            raise ValueError(f"Invalid memory type: {memory_type}. Must be one of: {list(self.MEMORY_TYPES.keys())}")

    def _get_memory_text(self, memory_type: str, memory_args: dict[str, Any]) -> str | None:
        """Extracts the text to embed from memory_args based on memory type."""
        text_field = self.get_text_field(memory_type)
        if text_field:
            memory_text = memory_args.get(text_field)
            if memory_text:
                return memory_text
        
        # Fallback: use any string value in memory_args
        for v in memory_args.values():
            if isinstance(v, str) and v.strip():
                return v
        return None

    def _create_memory_object(self, memory_type: str, memory_args: dict[str, Any]):
        """Creates a memory object instance based on type, auto-generating id if absent.

        For ProceduralUnit, ``name`` is a required constructor field but the AI
        may omit it.  Derive a sensible fallback from ``description`` so no memory
        is silently dropped.
        """
        if memory_type not in self.MEMORY_TYPES:
            if memory_type == "":
                return None
            raise ValueError(f"Unknown memory_type: {memory_type}")

        # id is an internal DB key — generate a UUID if the AI didn't provide one
        if 'id' not in memory_args:
            memory_args['id'] = str(uuid.uuid4())

        # ProceduralUnit.name is required — derive from description when absent
        if memory_type == "ProceduralUnit" and not memory_args.get("name"):
            description = memory_args.get("description", "")
            memory_args["name"] = (description[:60].rstrip() + "…") if len(description) > 60 else (description or "Unnamed procedure")

        cls = self.MEMORY_TYPES[memory_type][0]
        return cls(**memory_args)

    def _node_to_memory_object(self, node, expected_label: str = None):
        """Converts a Neo4j node to a memory object."""
        labels = list(node.labels) if hasattr(node, 'labels') else []
        label = labels[0] if labels else None
        
        if label in self.MEMORY_TYPES:
            cls = self.MEMORY_TYPES[label][0]
            return cls(**dict(node))
        return dict(node)

    async def _vector_search(self, query: str, filter_user_id: str | None, top_k: int) -> list[dict]:
        """Run vector search across all memory types. Returns list of {memory, score, memory_id, node_label} dicts.

        When filter_user_id is set, only returns memories authored by that person.
        When None, returns global results (all users' memories are visible).
        """
        driver = self.get_driver()
        query_embedding = await self._encode(query)
        results = []
        async with driver.session() as session:
            for node_label in self.MEMORY_TYPES:
                index_name = f"{node_label}EmbeddingIndex"
                vector_query = f"""
                CALL db.index.vector.queryNodes('{index_name}', $top_k, $query_embedding) YIELD node, score
                OPTIONAL MATCH (u:Person)-[:IS_AUTHOR_OF]->(node)
                WITH node, score, u
                WHERE $filter_user_id IS NULL OR (u IS NOT NULL AND u.id = $filter_user_id)
                OPTIONAL MATCH (node)-[:OCCURRED_AT]->(loc:Location)
                RETURN elementId(node) AS memoryId, node, score, u.id AS author_id, u.name AS author_name, loc.name AS location_name
                ORDER BY score DESC
                LIMIT $top_k
                """
                params = {"top_k": top_k, "query_embedding": query_embedding, "filter_user_id": filter_user_id}
                vector_results_cursor = await session.run(vector_query, params)
                vector_results = [record async for record in vector_results_cursor]
                for record in vector_results:
                    mem_obj = self._node_to_memory_object(record["node"], node_label)
                    results.append({
                        "memory": mem_obj,
                        "score": record["score"],
                        "memory_id": record["memoryId"],
                        "node_label": node_label,
                        "author_id": record.get("author_id"),
                        "author_name": record.get("author_name"),
                        "location_name": record.get("location_name"),
                    })
        return results

    async def add_memory(self, user_id: str, user_name: str, memory_type: str, memory_args: dict[str, Any], location_id: str | None = None):
        """
        Async method to add a memory node of a given type for a user.

        Uses domain classes for validation and Cypher generation.
        This method will complete before returning, ensuring workflow integrity.

        Args:
            user_id: Unique identifier for the user
            user_name: Display name of the user
            memory_type: Type of memory (must be in MEMORY_TYPES whitelist)
            memory_args: Properties for the memory node
            location_id: Optional location ID to link memory to a Location node
        """
        # Validate memory type against whitelist to prevent injection
        self._validate_memory_type(memory_type)

        # Generate embedding vector
        memory_text = self._get_memory_text(memory_type, memory_args)
        if memory_text:
            memory_args['summaryEmbeddingVector'] = await self._encode(memory_text)
        else:
            logging.warning(f"No memory text found for embedding in memory_args: {memory_args}")

        # Create memory object
        mem_obj = self._create_memory_object(memory_type, memory_args)
        if mem_obj is None:
            return  # No memory type provided

        driver = self.get_driver()
        async with driver.session() as session:
            # Create memory and author relationship using parameterized queries
            person_obj = Person(user_id, user_name)
            person_label = person_obj.get_label()
            person_props = person_obj.get_properties()

            memory_label = mem_obj.get_label()
            memory_props = mem_obj.get_properties()

            query = f"""
            MERGE (u:{person_label} {{id: $person_id}})
            ON CREATE SET u += $person_props
            ON MATCH SET u += $person_props
            CREATE (m:{memory_label} $memory_props)
            MERGE (u)-[:IS_AUTHOR_OF]->(m)
            """
            await session.run(query, {
                "person_id": person_props["id"],
                "person_props": person_props,
                "memory_props": memory_props,
            })

            mem_id = getattr(mem_obj, 'id', None)

            # Link concepts
            concepts = memory_args.get('concepts', [])
            if concepts:
                for concept_data in concepts:
                    # concept_data can be a string (name) or dict
                    if isinstance(concept_data, str):
                        concept_obj = Concept(id=None, name=concept_data)
                    elif isinstance(concept_data, dict):
                        concept_obj = Concept(**concept_data)
                    else:
                        continue

                    # Merge concept node using parameterized query
                    concept_label = concept_obj.get_label()
                    concept_props = concept_obj.get_properties()
                    await session.run(
                        f"MERGE (c:{concept_label} {{name: $concept_name}}) ON CREATE SET c += $concept_props ON MATCH SET c += $concept_props",
                        {"concept_name": concept_obj.name, "concept_props": concept_props},
                    )

                    # Link memory to concept using parameterized query
                    if mem_id:
                        cypher_link = f"""
                        MATCH (m:{memory_label} {{id: $mem_id}})
                        WITH m
                        MATCH (c:CONCEPT {{name: $concept_name}})
                        MERGE (m)-[:REFERS_TO_CONCEPT]->(c)
                        """
                        await session.run(cypher_link,
                                  {"mem_id": mem_id, "concept_name": concept_obj.name})
                    else:
                        logging.warning(
                            f"Cannot link concept '{concept_obj.name}' — "
                            f"memory has no id"
                        )
                        continue
                    logging.info(f"Linked memory to concept: {concept_obj.name}")

            # Link location
            if location_id and mem_id:
                cypher_loc = f"""
                MATCH (m:{memory_label} {{id: $mem_id}})
                WITH m
                MATCH (l:Location {{location_id: $location_id}})
                MERGE (m)-[:OCCURRED_AT]->(l)
                """
                await session.run(cypher_loc, {"mem_id": mem_id, "location_id": location_id})
                logging.info(f"Linked memory {mem_id} to location: {location_id}")

    async def search(self, query: str, top_k: int = 5, filter_user_id: str | None = None) -> list:
        """
        Async method to search relevant memories based on vector similarity.

        This method will complete before returning, ensuring workflow integrity.

        Args:
            query: Search query text
            top_k: Number of results to return
            filter_user_id: Optional. Filter results to memories authored by a specific
                            person (scoped user ID like 'discord:123'). None = global search.

        Returns:
            List of (memory_object, similarity_score) tuples
        """
        results = await self._vector_search(query, filter_user_id, top_k)
        return [(r["memory"], r["score"]) for r in results]


    async def search_with_context(self, query: str, top_k: int = 3, context_k: int = 5, filter_user_id: str | None = None) -> list:
        """
        Async method to search memories with graph context expansion.

        Uses vector similarity and graph traversal to find relevant memories
        and their related context. This method will complete before returning.

        Args:
            query: Search query text
            top_k: Number of vector search results
            context_k: Number of context expansion results

        Returns:
            List of dicts with memory, score, and type information
        """
        logging.info(f"search_with_context: query='{query}'")
        results = await self._vector_search(query, filter_user_id, top_k)
        output = []
        memory_ids = []

        for r in results:
            output.append({"memory": r["memory"], "score": r["score"], "type": "vector"})
            memory_ids.append(r["memory_id"])

        logging.info(f"search_with_context: collected memory_ids={memory_ids}")

        # Step 2: Context expansion via edges
        if memory_ids:
            driver = self.get_driver()
            async with driver.session() as session:
                context_query = """
                MATCH (m)-[r]->(related)
                WHERE elementId(m) IN $memoryIds AND NOT elementId(related) IN $memoryIds
                AND (related:EpisodicMemory OR related:KnowledgeUnit OR related:ProceduralUnit)
                RETURN DISTINCT elementId(related) AS memoryId, related
                LIMIT $context_k
                """
                context_params = {"memoryIds": memory_ids, "context_k": context_k}
                context_cursor = await session.run(context_query, context_params)
                context_results = [record async for record in context_cursor]
                logging.info(f"search_with_context: context_results: {context_results}")

                for record in context_results:
                    mem_obj = self._node_to_memory_object(record["related"])
                    logging.info(f"search_with_context: context node={record['related']}")
                    output.append({"memory": mem_obj, "score": 0.0, "type": "context"})
        else:
            logging.info("search_with_context: No memory_ids found from vector search.")

        return output

    async def setup_indices(self):
        """
        Async method to ensure necessary indices exist in Neo4j.

        Creates user ID index and vector indices for all memory types.
        If an existing vector index has a different dimension than the configured
        model, it is dropped and all stale embedding vectors are cleared so that
        regenerate_missing_embeddings() can rebuild them.
        Must be called from async context after initialization.
        """
        driver = self.get_driver()
        async with driver.session(database="neo4j") as session:
            # Create user ID index
            try:
                await session.run("CREATE INDEX userIdIndex IF NOT EXISTS FOR (u:Person) ON (u.id)")
            except Exception as e:
                logging.warning(f"Could not create/check User ID index: {e}")

            # Create / migrate vector index for each memory type
            for node_label in self.MEMORY_TYPES.keys():
                index_name = f"{node_label}EmbeddingIndex"

                # Check if index exists and read its configured dimension
                cursor = await session.run(
                    "SHOW INDEXES YIELD name, options WHERE name = $name",
                    name=index_name
                )
                rows = [r async for r in cursor]

                if rows:
                    existing_dim = (
                        rows[0]["options"]
                        .get("indexConfig", {})
                        .get("vector.dimensions")
                    )
                    if existing_dim is not None and int(existing_dim) != self.dimension:
                        logging.warning(
                            f"Index '{index_name}' has dimension {existing_dim} but "
                            f"model requires {self.dimension}. Dropping index and clearing "
                            "stale embeddings — will regenerate on startup."
                        )
                        await session.run(f"DROP INDEX {index_name}")
                        await session.run(
                            f"MATCH (m:{node_label}) REMOVE m.summaryEmbeddingVector"
                        )
                        rows = []  # Force re-creation below

                if not rows:
                    await session.run(f"""
                        CREATE VECTOR INDEX {index_name} IF NOT EXISTS
                        FOR (m:{node_label}) ON (m.summaryEmbeddingVector)
                        OPTIONS {{ indexConfig: {{
                            `vector.dimensions`: {self.dimension},
                            `vector.similarity_function`: 'cosine'
                        }}}}
                    """)
        

    async def regenerate_missing_embeddings(self) -> None:
        """Re-embed all memory nodes that are missing a summaryEmbeddingVector.

        Run this as a background task after setup_indices(). Safe to call at any
        time — it only touches nodes that have no vector yet, so re-running is
        idempotent.
        """
        driver = self.get_driver()
        total = 0
        for node_label, (_, text_field) in self.MEMORY_TYPES.items():
            async with driver.session() as session:
                cursor = await session.run(
                    f"MATCH (m:{node_label}) WHERE m.summaryEmbeddingVector IS NULL RETURN elementId(m) AS id, m.{text_field} AS text"
                )
                nodes = [r async for r in cursor]

            for record in nodes:
                text = record.get("text")
                if not text:
                    continue
                try:
                    vector = await self._encode(text)
                    async with driver.session() as session:
                        await session.run(
                            f"MATCH (m:{node_label}) WHERE elementId(m) = $id SET m.summaryEmbeddingVector = $vec",
                            id=record["id"], vec=vector
                        )
                    total += 1
                except Exception as e:
                    logging.warning(f"Failed to embed {node_label} node {record['id']}: {e}")

        if total:
            logging.info(f"Re-embedded {total} memory node(s) with model '{self.embedding_model}'.")
        else:
            logging.info("All memory nodes already have embeddings — nothing to regenerate.")

    async def clear(self, user_id: str | None = None):
        """
        Async method to clear memories from the database.

        Deletes memories for a specific user or all memories.
        Also cleans up orphaned concept nodes.

        Args:
            user_id: Optional user ID to filter deletion (None = clear all)
        """
        driver = self.get_driver()
        async with driver.session() as session:
            if user_id:
                await session.run("MATCH (u:Person {id: $userId})-[r:IS_AUTHOR_OF]->(m) DETACH DELETE m", {"userId": user_id})
            else:
                # Note: Label names cannot be parameterized in MATCH clauses,
                # but we're only using whitelisted MEMORY_TYPES keys
                for label in self.MEMORY_TYPES.keys():
                    await session.run(f"MATCH (m:{label}) DETACH DELETE m")
                await session.run("MATCH (c:CONCEPT) WHERE NOT EXISTS((c)<-[:REFERS_TO_CONCEPT]-()) DETACH DELETE c")

    async def get_total_entries(self, user_id: str | None = None) -> int:
        """
        Async method to count total memory entries.

        Args:
            user_id: Optional user ID to filter count (None = count all)

        Returns:
            Total number of memory entries
        """
        driver = self.get_driver()
        async with driver.session() as session:
            if user_id:
                result_cursor = await session.run("MATCH (u:Person {id: $userId})-[:IS_AUTHOR_OF]->(m) RETURN count(m)", {"userId": user_id})
                record = await result_cursor.single()
                count = record[0] if record else 0
                return count if count is not None else 0
            else:
                total = 0
                # Note: Label names cannot be parameterized in MATCH clauses,
                # but we're only using whitelisted MEMORY_TYPES keys
                for label in self.MEMORY_TYPES.keys():
                    result_cursor = await session.run(f"MATCH (m:{label}) RETURN count(m)")
                    record = await result_cursor.single()
                    count = record[0] if record else 0
                    if count:
                        total += count
                return total