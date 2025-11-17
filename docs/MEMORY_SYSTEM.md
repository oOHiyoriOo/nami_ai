# Memory System

The Personality Proxy uses Neo4j as a graph database for long-term memory, enabling AI personalities to remember past interactions, learn from conversations, and maintain context across sessions.

## Overview

The memory system consists of:

- **Neo4j Graph Database** - Stores memories as nodes with relationships
- **Vector Embeddings** - Semantic search using sentence transformers
- **Three Memory Types** - Episodic, knowledge, and procedural memories
- **Automatic Extraction** - Memories created from conversations automatically
- **Context Retrieval** - Relevant memories added to prompts

## Memory Types

### 1. Episodic Memory

**Purpose:** Experiences, events, and interactions with emotional context.

**Structure:**
```python
{
  "type": "EpisodicMemory",
  "summary": "User shared their cat's birthday",
  "concepts": ["cat", "birthday", "celebration"],
  "authorUserId": "alice",
  "emotionalContext": "happy",
  "importance": 0.7,
  "creationTimestamp": 1705500000000
}
```

**Examples:**
- "User mentioned they're learning Python"
- "Had a great conversation about space exploration"
- "User was frustrated with their code not working"

**Use Cases:**
- Personal details about users
- Interaction history
- Emotional moments
- Shared experiences

### 2. Knowledge Units

**Purpose:** Factual information, preferences, and statements.

**Structure:**
```python
{
  "type": "KnowledgeUnit",
  "summary": "User prefers dark mode in IDEs",
  "concepts": ["preferences", "IDE", "dark mode"],
  "authorUserId": "alice",
  "importance": 0.8,
  "creationTimestamp": 1705500000000
}
```

**Examples:**
- "User is a software developer"
- "User's favorite programming language is Python"
- "User lives in San Francisco"
- "User prefers concise explanations"

**Use Cases:**
- User preferences
- Factual knowledge
- Persistent facts
- User context

### 3. Procedural Units

**Purpose:** Skills, processes, workflows, and how-to information.

**Structure:**
```python
{
  "type": "ProceduralUnit",
  "summary": "User's workflow for deploying apps",
  "concepts": ["deployment", "workflow", "automation"],
  "steps": ["Build", "Test", "Deploy", "Monitor"],
  "authorUserId": "alice",
  "importance": 0.6,
  "creationTimestamp": 1705500000000
}
```

**Examples:**
- "User's debugging process"
- "How user organizes their projects"
- "User's preferred git workflow"
- "Steps user follows for code review"

**Use Cases:**
- Learned processes
- User workflows
- Skill tracking
- Methodology preferences

## How It Works

### 1. Memory Creation

Memories are created automatically during conversations:

```
User: "I love hiking in the mountains"
  ↓
AI processes message
  ↓
vector_helper extracts important info
  ↓
Memory created:
  Type: KnowledgeUnit
  Summary: "User enjoys hiking in mountains"
  Concepts: ["hiking", "mountains", "outdoor activities"]
  ↓
Stored in Neo4j with vector embedding
```

### 2. Memory Retrieval

When a new message arrives:

```
User: "What outdoor activities do you recommend?"
  ↓
Message embedding created
  ↓
Neo4j vector search (top_k=5)
  ↓
Retrieved: "User enjoys hiking in mountains" (score: 0.85)
  ↓
Added to prompt context
  ↓
AI response uses this information
```

### 3. Relevance Scoring

Memories are scored by:
- **Cosine similarity** (0.0 - 1.0)
- **Recency** (newer = higher score)
- **Importance** (manually set 0.0 - 1.0)
- **Access frequency** (often accessed = important)

**Threshold:** Default 0.65 (configurable in code)

## Configuration

### Neo4j Setup

**Docker:**
```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password \
  -v neo4j_data:/data \
  neo4j:latest
```

**Configuration:**
```yaml
neo4j:
  uri: bolt://localhost:7687
  user: neo4j
  pass: your_password

memory_db:
  model: all-MiniLM-L6-v2  # Embedding model
```

### Embedding Models

Available models (via sentence-transformers):

| Model | Dimensions | Size | Speed | Quality |
|-------|-----------|------|-------|---------|
| `all-MiniLM-L6-v2` | 384 | 80MB | Fast | Good |
| `all-mpnet-base-v2` | 768 | 420MB | Medium | Better |
| `all-distilroberta-v1` | 768 | 290MB | Medium | Better |

**Recommendation:** `all-MiniLM-L6-v2` for most uses (good balance).

### Memory Parameters

In code (`api_server.py`):

```python
# Number of memories to retrieve
retrieved_memories = memory_db.search_with_context(
    query=message,
    top_k=5,         # Top 5 most relevant
    context_k=20     # Search pool of 20
)

# Relevance threshold
similarity_threshold = 0.65  # 0.0 - 1.0
```

## API Usage

### Automatic (Default)

Memory is enabled by default:

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "llama2",
  "messages": [{"role": "user", "content": "Hello"}],
  "user_id": "alice"
}'
```

Memories for user "alice" are:
- ✅ Retrieved automatically
- ✅ Added to context
- ✅ Created from conversation

### Disable Memory

For faster responses without memory:

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "llama2",
  "messages": [{"role": "user", "content": "Hello"}],
  "user_id": "alice",
  "enable_memory": false
}'
```

### Per-User Memory

Each `user_id` has separate memories:

```bash
# Alice's memories
curl ... -d '{"user_id": "alice", ...}'

# Bob's memories (separate from Alice)
curl ... -d '{"user_id": "bob", ...}'
```

### Per-Conversation Context

Use `conversation_id` for conversation-specific context:

```bash
curl ... -d '{
  "user_id": "alice",
  "conversation_id": "work_chat",
  ...
}'
```

## Viewing Memories

### Neo4j Browser

1. Open http://localhost:7474
2. Login with credentials
3. Run queries:

```cypher
// View all memories
MATCH (n) RETURN n LIMIT 25

// View memories for a user
MATCH (n {authorUserId: "alice"}) RETURN n

// View episodic memories
MATCH (n:EpisodicMemory) RETURN n

// Count memories by type
MATCH (n) RETURN labels(n)[0] as type, count(*) as count

// Find memories about a topic
MATCH (n)
WHERE n.summary CONTAINS "hiking"
RETURN n
```

### Python Script

```python
from lib.memory_db import MemoryDb

# Connect
memory_db = MemoryDb(
    neo4j_uri="bolt://localhost:7687",
    neo4j_user="neo4j",
    neo4j_pass="your_password"
)

# Search memories
results = memory_db.search_with_context(
    query="hiking",
    top_k=10
)

for mem in results:
    print(f"{mem['type']}: {mem['text']} (score: {mem['score']:.2f})")

# Get total count
total = memory_db.get_total_entries()
print(f"Total memories: {total}")
```

## Memory Management

### Clear All Memories

```cypher
// In Neo4j Browser
MATCH (n) DETACH DELETE n
```

### Clear User's Memories

```cypher
MATCH (n {authorUserId: "alice"}) DETACH DELETE n
```

### Export Memories

```cypher
// Export as JSON
MATCH (n {authorUserId: "alice"})
RETURN n
```

### Backup Database

```bash
# Stop Neo4j
docker stop neo4j

# Backup data volume
docker run --rm \
  -v neo4j_data:/data \
  -v $(pwd):/backup \
  ubuntu tar czf /backup/neo4j_backup.tar.gz /data

# Restart Neo4j
docker start neo4j
```

## Advanced Features

### Memory Importance

Set importance when creating memories manually:

```python
memory_args = {
    "summary": "Critical user preference",
    "importance": 0.9,  # 0.0 - 1.0
    "authorUserId": "alice",
    ...
}

memory_db.add_memory(
    user_id="alice",
    user_name="alice",
    memory_type="KnowledgeUnit",
    memory_args=memory_args
)
```

Higher importance → more likely to be retrieved.

### Context Expansion

The `context_k` parameter expands search:

```python
# Retrieve top 5 from pool of 20
retrieved = memory_db.search_with_context(
    query="hiking",
    top_k=5,        # Return top 5
    context_k=20    # Search top 20
)
```

This includes:
- Direct matches (vector similarity)
- Related memories (graph neighbors)
- Context memories (connected nodes)

### Temporal Decay

Older memories naturally get lower scores:

- Recency is factored into relevance
- More recent memories preferred
- Old memories can still rank high if very relevant

### Memory Relationships

Neo4j stores relationships between memories:

```cypher
// Create relationship
MATCH (a:KnowledgeUnit {summary: "User likes Python"})
MATCH (b:EpisodicMemory {summary: "Helped debug Python code"})
CREATE (a)-[:RELATED_TO]->(b)

// Query relationships
MATCH (a)-[r]-(b)
WHERE a.authorUserId = "alice"
RETURN a, r, b
```

## Troubleshooting

### No Memories Retrieved

**Check Neo4j connection:**
```bash
cypher-shell -a bolt://localhost:7687 -u neo4j -p password
```

**Check memories exist:**
```cypher
MATCH (n) RETURN count(n)
```

**Lower threshold** in code:
```python
similarity_threshold = 0.5  # Lower from 0.65
```

**Check embedding model** is downloaded:
```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('all-MiniLM-L6-v2')
```

### Memories Not Created

**Check logs:**
```bash
tail -f logs/*.log | grep -i memory
```

**Verify vector_helper** is working:
```python
from lib.vector_helper import VectorHelper

helper = VectorHelper(None, config)
memories = await helper.extract_important_information(
    "User mentioned they like hiking"
)
print(memories)
```

**Check enable_memory** is true in request.

### Slow Performance

**Reduce memories retrieved:**
```python
top_k=3,        # From 5
context_k=10    # From 20
```

**Disable memory** for some requests:
```json
{"enable_memory": false}
```

**Use faster embedding model:**
```yaml
memory_db:
  model: all-MiniLM-L6-v2  # Fastest
```

**Add Neo4j indexes:**
```cypher
CREATE INDEX FOR (n:EpisodicMemory) ON (n.authorUserId);
CREATE INDEX FOR (n:KnowledgeUnit) ON (n.authorUserId);
CREATE INDEX FOR (n:ProceduralUnit) ON (n.authorUserId);
```

## Best Practices

### 1. Use Meaningful user_ids

```bash
# Good
user_id: "alice@example.com"
user_id: "user_12345"

# Avoid
user_id: "anonymous"
user_id: "test"
```

### 2. Set Appropriate Importance

```python
# Critical preferences
importance: 0.9

# Normal facts
importance: 0.5-0.7

# Trivial info
importance: 0.1-0.3
```

### 3. Regular Cleanup

```cypher
// Delete old low-importance memories
MATCH (n)
WHERE n.importance < 0.3
  AND n.creationTimestamp < timestamp() - 7776000000  // 90 days
DETACH DELETE n
```

### 4. Monitor Memory Count

```python
total = memory_db.get_total_entries()
if total > 10000:
    logging.warning("High memory count, consider cleanup")
```

### 5. Backup Regularly

```bash
# Weekly backup
0 0 * * 0 docker run --rm -v neo4j_data:/data -v /backups:/backup ubuntu tar czf /backup/neo4j_$(date +\%Y\%m\%d).tar.gz /data
```

## See Also

- [Quick Start](QUICKSTART.md) - Get started
- [API Reference](API_REFERENCE.md) - API docs
- [Tools](TOOLS.md) - Memory tools

---

Need help? [Open an issue](https://github.com/oOHiyoriOo/nami_ai/issues) on GitHub.
