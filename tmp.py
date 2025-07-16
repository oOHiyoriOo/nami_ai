from sentence_transformers import SentenceTransformer
sentences = "I listen to whatever I like."

model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
embeddings = model.encode(sentences).tolist()

print(f"CALL db.index.vector.queryNodes('KnowledgeUnitEmbeddingIndex', 10, {str(embeddings)}) YIELD node, score \
RETURN elementId(node) AS memoryId, node, score \
ORDER BY score DESC \
LIMIT 10")
