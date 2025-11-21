from sentence_transformers import SentenceTransformer
from neo4j import GraphDatabase # Neo4j Python Driver
from typing import Any

from lib.neo4j_lib.episodic_memory import EpisodicMemory
from lib.neo4j_lib.knowledge_unit import KnowledgeUnit
from lib.neo4j_lib.procedural_unit import ProceduralUnit

import logging
import numpy as np
import torch

# Entfernt: faiss, pickle, os, json (json wird eher im vector_helper gebraucht)

class MemoryDb:
    # Angepasster Konstruktor für Neo4j
    def __init__(self, neo4j_uri: str, neo4j_user: str, neo4j_pass: str, model_name: str = 'all-MiniLM-L6-v2'):
        """
        Initializes the Memory Database connection using Neo4j and SentenceTransformers.

        Args:
            neo4j_uri (str): URI for the Neo4j database (e.g., "neo4j://localhost:7687" or "neo4j+s://your-aura-instance.databases.neo4j.io").
            neo4j_user (str): Username for Neo4j connection (e.g., "neo4j").
            neo4j_pass (str): Password for Neo4j connection.
            model_name (str): Name of the SentenceTransformers model for embeddings.
        """
        # Sentence Transformer Model laden (wird weiterhin für Embeddings gebraucht)
        self.model = SentenceTransformer(model_name, device='cuda' if torch.cuda.is_available() else 'cpu')
        self.dimension = self.model.get_sentence_embedding_dimension()
        if not self.dimension:
            raise ValueError(f"Could not determine embedding dimension for model '{model_name}'.")
        
        logging.info(f"SentenceTransformer model '{model_name}' loaded with dimension {self.dimension}.")

        # Neo4j Verbindung initialisieren
        self.neo4j_uri = neo4j_uri
        self.neo4j_auth = (neo4j_user, neo4j_pass)
        self._driver = None
        logging.info(f"Neo4j driver configured for URI: {self.neo4j_uri}")
        self.setup_indices()
        logging.info(f"MemoryDb initialized for Neo4j.")

    def get_driver(self):
        """Stellt sicher, dass ein aktiver Treiber vorhanden ist."""
        if self._driver is None or self._driver._closed:
            self._driver = GraphDatabase.driver(self.neo4j_uri, auth=self.neo4j_auth)
            logging.info("Neo4j driver created.")
        return self._driver

    def close(self):
        """Schließt die Neo4j Treiberverbindung."""
        if self._driver is not None and not self._driver._closed:
            self._driver.close()
            logging.info("Neo4j driver closed.")

    def add_memory(self, user_id: str, user_name: str, memory_type: str, memory_args: dict[str, Any]):
        """
        Adds a memory node of a given type for a user, with properties from memory_args.
        Uses domain classes for validation and Cypher generation.
        """
        from lib.neo4j_lib.episodic_memory import EpisodicMemory
        from lib.neo4j_lib.knowledge_unit import KnowledgeUnit
        from lib.neo4j_lib.procedural_unit import ProceduralUnit
        from lib.neo4j_lib.person import Person
        from lib.neo4j_lib.concept import Concept
        from lib.neo4j_lib.location import Location



        # Generate summaryEmbeddingVector from the correct field per type
        memory_text = None
        if memory_type == "EpisodicMemory":
            memory_text = memory_args.get('summary')
        elif memory_type == "KnowledgeUnit":
            memory_text = memory_args.get('statement')
        elif memory_type == "ProceduralUnit":
            memory_text = memory_args.get('description')
        # Fallback: use any string value in memory_args
        if not memory_text:
            for v in memory_args.values():
                if isinstance(v, str) and v.strip():
                    memory_text = v
                    break
        if memory_text:
            memory_args['summaryEmbeddingVector'] = self.model.encode(memory_text).tolist()
        else:
            logging.warning(f"No memory text found for embedding in memory_args: {memory_args}")

        driver = self.get_driver()
        with driver.session() as session:
            if memory_type == "EpisodicMemory":
                mem_obj = EpisodicMemory(**memory_args)
                cypher = mem_obj.to_cypher("m")
            elif memory_type == "KnowledgeUnit":
                mem_obj = KnowledgeUnit(**memory_args)
                cypher = mem_obj.to_cypher("m")
            elif memory_type == "ProceduralUnit":
                mem_obj = ProceduralUnit(**memory_args)
                cypher = mem_obj.to_cypher("m")
            elif memory_type == "":
                return  # No memory type provided, do nothing
            else:
                raise ValueError(f"Unknown memory_type: {memory_type}")

            # Create or update Person node
            person_obj = Person(user_id, user_name)
            person_cypher = person_obj.to_cypher("u")

            # Merge memory node and author relationship
            query = f"""
            MERGE {person_cypher}
            CREATE {cypher}
            MERGE (u)-[:IST_AUTOR_VON]->(m)
            """
            session.run(query)

            # Link concepts as separate nodes using Concept class
            concepts = memory_args.get('concepts', [])
            if concepts:
                from lib.neo4j_lib.concept import Concept
                mem_id = getattr(mem_obj, 'id', None)
                for concept_data in concepts:
                    # concept_data can be a string (name) or dict
                    if isinstance(concept_data, str):
                        concept_obj = Concept(id=None, name=concept_data)
                    elif isinstance(concept_data, dict):
                        concept_obj = Concept(**concept_data)
                    else:
                        continue
                    concept_cypher = concept_obj.to_cypher("c")
                    # Merge concept node
                    session.run(f"MERGE {concept_cypher}")
                    # Link memory to concept (avoid cartesian product)
                    if mem_id:
                        cypher_link = f"""
                        MATCH (m:{memory_type} {{id: $mem_id}})
                        WITH m
                        MATCH (c:CONCEPT {{name: $concept_name}})
                        MERGE (m)-[:BEZIEHT_SICH_AUF_KONZEPT]->(c)
                        """
                        session.run(cypher_link, {"mem_id": mem_id, "concept_name": concept_obj.name})
                    else:
                        cypher_link = f"""
                        MATCH (m:{memory_type})
                        WITH m
                        MATCH (c:CONCEPT {{name: $concept_name}})
                        MERGE (m)-[:BEZIEHT_SICH_AUF_KONZEPT]->(c)
                        """
                        session.run(cypher_link, {"concept_name": concept_obj.name})
                    logging.info(f"Linked memory to concept: {concept_obj.name}")

    # Search returns memory objects and similarity scores
    def search(self, query: str, user_id: str | None = None, top_k: int = 5) -> list:
        """
        Searches relevant memories based on a query using vector similarity in Neo4j.
        Returns list of (memory_object, similarity_score).
        """
        from lib.neo4j_lib.episodic_memory import EpisodicMemory
        from lib.neo4j_lib.knowledge_unit import KnowledgeUnit
        from lib.neo4j_lib.procedural_unit import ProceduralUnit
        driver = self.get_driver()
        with driver.session() as session:
            query_embedding = self.model.encode(query).tolist()
            output = []
            # Vector search for all types
            for node_label, cls in [
                ("EpisodicMemory", EpisodicMemory),
                ("KnowledgeUnit", KnowledgeUnit),
                ("ProceduralUnit", ProceduralUnit)
            ]:
                index_name = f"{node_label}EmbeddingIndex"
                cypher_query = f"""
                CALL db.index.vector.queryNodes('{index_name}', $top_k, $query_embedding) YIELD node, score
                RETURN node, score
                ORDER BY score DESC
                LIMIT $top_k
                """
                params = {"top_k": top_k, "query_embedding": query_embedding}
                results = session.run(cypher_query, params)
                for record in results:
                    node = record["node"]
                    score = record["score"]
                    labels = list(node.labels) if hasattr(node, 'labels') else []
                    label = labels[0] if labels else None
                    if label == node_label:
                        mem_obj = cls(**dict(node))
                    else:
                        mem_obj = dict(node)
                    output.append((mem_obj, score))
            return output


    def search_with_context(self, query: str, top_k: int = 3, context_k: int = 5) -> list:
        """
        Searches relevant memories globally based on a query using vector similarity
        and graph context traversal in Neo4j. Returns memory objects and context.
        """
        driver = self.get_driver()
        with driver.session() as session:
            query_embedding = self.model.encode(query).tolist()
            logging.info(f"search_with_context: query='{query}'")
            output = []
            memory_ids = []
            # Step 1: Vector search for all types
            for node_label, cls in [
                ("EpisodicMemory", EpisodicMemory),
                ("KnowledgeUnit", KnowledgeUnit),
                ("ProceduralUnit", ProceduralUnit)
            ]:
                index_name = f"{node_label}EmbeddingIndex"
                vector_query = f"""
                CALL db.index.vector.queryNodes('{index_name}', $top_k, $query_embedding) YIELD node, score
                RETURN elementId(node) AS memoryId, node, score
                ORDER BY score DESC
                LIMIT $top_k
                """
                params = {"top_k": top_k, "query_embedding": query_embedding}
                vector_results = list(session.run(vector_query, params))
                for record in vector_results:
                    node = record["node"]
                    score = record["score"]
                    labels = list(node.labels) if hasattr(node, 'labels') else []
                    label = labels[0] if labels else None
                    
                    if label == node_label:
                        mem_obj = cls(**dict(node))
                    else:
                        mem_obj = dict(node)

                    output.append({"memory": mem_obj, "score": score, "type": "vector"})
                    memory_ids.append(record["memoryId"])

            logging.info(f"search_with_context: collected memory_ids={memory_ids}")

            # Step 2: Context expansion via edges
            if memory_ids:
                context_query = """
                MATCH (m)-[r]->(related)
                WHERE elementId(m) IN $memoryIds AND NOT elementId(related) IN $memoryIds
                AND (related:EpisodicMemory OR related:KnowledgeUnit OR related:ProceduralUnit)
                RETURN DISTINCT elementId(related) AS memoryId, related
                LIMIT $context_k
                """

                context_params = {"memoryIds": memory_ids, "context_k": context_k}
                context_results = list(session.run(context_query, context_params))
                logging.info(f"search_with_context: context_results: {context_results}")

                for record in context_results:
                    node = record["related"]
                    labels = list(node.labels) if hasattr(node, 'labels') else []
                    label = labels[0] if labels else None
                    logging.info(f"search_with_context: context node_label={label}, node={node}")
                    if label == "EpisodicMemory":
                        mem_obj = EpisodicMemory(**dict(node))
                    elif label == "KnowledgeUnit":
                        mem_obj = KnowledgeUnit(**dict(node))
                    elif label == "ProceduralUnit":
                        mem_obj = ProceduralUnit(**dict(node))
                    else:
                        mem_obj = dict(node)
                    output.append({"memory": mem_obj, "score": 0.0, "type": "context"})
            else:
                logging.info("search_with_context: No memory_ids found from vector search.")

            return output

    def setup_indices(self):
        """
        Ensures necessary indices exist in Neo4j, especially the vector index.
        """
        driver = self.get_driver()
        with driver.session(database="neo4j") as session:
            try:
                session.run("CREATE INDEX userIdIndex IF NOT EXISTS FOR (u:Person) ON (u.id)")
            except Exception as e:
                logging.warning(f"Could not create/check User ID index: {e}")

            property_name = 'summaryEmbeddingVector'
            similarity_function = 'cosine'
            # Create vector index for each memory type
            for node_label in ["EpisodicMemory", "KnowledgeUnit", "ProceduralUnit"]:
                index_name = f"{node_label}EmbeddingIndex"
                query = "SHOW INDEXES YIELD name WHERE name = $name"
                result = list(session.run(query, name=index_name))
                if not result:
                    session.run(f"""
                        CREATE VECTOR INDEX {index_name} IF NOT EXISTS
                        FOR (m:{node_label}) ON (m.{property_name})
                        OPTIONS {{ indexConfig: {{
                            `vector.dimensions`: {self.dimension},
                            `vector.similarity_function`: '{similarity_function}'
                        }}}}
                    """)
        

    # Angepasste 'clear' Methode
    def clear(self, user_id: str | None = None):
        """
        Clears memories (and related concepts if they become orphaned) from the database.
        """
        driver = self.get_driver()
        with driver.session() as session:
            if user_id:
                query = "MATCH (u:Person {id: $userId})-[r:IST_AUTOR_VON]->(m) DETACH DELETE m"
                session.run(query, userId=user_id)
            else:
                session.run("MATCH (m:EpisodicMemory) DETACH DELETE m")
                session.run("MATCH (m:KnowledgeUnit) DETACH DELETE m")
                session.run("MATCH (m:ProceduralUnit) DETACH DELETE m")
                session.run("MATCH (c:CONCEPT) WHERE NOT EXISTS((c)<-[:BEZIEHT_SICH_AUF_KONZEPT]-()) DETACH DELETE c")
       

    # Angepasste 'get_total_entries'
    def get_total_entries(self, user_id: str | None = None) -> int:
        """
        Returns the total number of stored memory entries.
        """
        driver = self.get_driver()
        with driver.session() as session:
            if user_id:
                query = "MATCH (u:Person {id: $userId})-[:IST_AUTOR_VON]->(m) RETURN count(m)"
                result = session.run(query, userId=user_id)
                count = result.single()[0]
                return count if count is not None else 0
            else:
                total = 0
                for label in ["EpisodicMemory", "KnowledgeUnit", "ProceduralUnit"]:
                    query = f"MATCH (m:{label}) RETURN count(m)"
                    result = session.run(query)
                    count = result.single()[0]
                    if count:
                        total += count
                return total

    def __del__(self):
        self.close()