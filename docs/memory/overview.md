# Memory System

Nami uses Neo4j as a **relationship-rich long-term memory graph**. The goal is not just semantic retrieval of old messages; it is a personal assistant that remembers **people, places, events, preferences, and cross-platform identity**.

## Overview

The memory system combines:

- **Neo4j graph storage** for people, memories, concepts, and locations
- **Vector embeddings** for semantic retrieval across memory nodes
- **Three memory types**: episodic, knowledge, and procedural
- **Automatic extraction** from conversation turns via `memory_extractor.py`
- **Cross-platform identity resolution** via `SAME_PERSON_AS`
- **Context injection** through `context_builder.py`

## Memory Types

### 1. Episodic Memory

Experiences, events, and interactions.

**Typical fields:**
```python
{
  "id": "uuid",
  "summary": "Met Sarah at Example Lab after the robotics demo.",
  "concepts": ["robotics", "demo"],
  "emotionalContext": "excited",
  "importance": 0.7,
  "creationTimestamp": "2026-05-09T12:00:00+00:00"
}
```

### 2. Knowledge Units

Facts, preferences, and stable statements.

**Typical fields:**
```python
{
  "id": "uuid",
  "statement": "<username> prefers dark mode in IDEs.",
  "importance": 0.8,
  "creationTimestamp": "2026-05-09T12:00:00+00:00"
}
```

### 3. Procedural Units

Workflows, habits, and reusable processes.

**Typical fields:**
```python
{
  "id": "uuid",
  "description": "<username> deploys by build → test → ship.",
  "steps": ["Build", "Test", "Ship"],
  "importance": 0.6,
  "creationTimestamp": "2026-05-09T12:00:00+00:00"
}
```

## Person Nodes

`(:Person)` nodes anchor identity in the graph.

**Properties:**
- `id` - scoped identity such as `discord:123456789`
- `name` - human-readable display name
- `nickname` - optional alias

**Relationships:**
- `(Person)-[:IS_AUTHOR_OF]->(Memory)` for the person who said or caused the memory
- `(Memory)-[:IS_ABOUT]->(Person)` for third-party facts stored by `remember_about_person.py`
- `(Person)-[:SAME_PERSON_AS]-(Person)` for linked identities across platforms

There are two common kinds of people in the graph:
1. **Direct users** with scoped IDs like `discord:123`
2. **Third parties** created with slugified IDs such as `sarah-connor`

## Location Nodes

`(:Location)` nodes store where something happened.

**Properties:**
- `location_id` - slugified stable key
- `name` - display name such as `Example Lab`
- `description` - optional explanation of the place

**Relationship:**
- `(Memory)-[:OCCURRED_AT]->(Location)`

Location extraction is driven by the `FACT_RETRIEVAL.md` prompt. `memory_extractor.py` returns `locations`, and `memory_processor.py`:

1. upserts each location with `add_location()`
2. heuristically links a memory to the matching location if the location name appears in the extracted memory text

That means Nami can remember not just *what* happened, but *where*.

## Cross-Platform Identity

Nami uses scoped IDs so the same human can appear on multiple platforms:

- `discord:123456789`
- `whatsapp:+15551234567`
- `api:<username>`

To merge those identities, `memory_db.link_person_identities()` creates:

```text
(Person)-[:SAME_PERSON_AS]-(Person)
```

At retrieval time:

1. `context_builder.py` calls `resolve_canonical_users(user_id)`
2. Neo4j traverses `SAME_PERSON_AS*0..`
3. `memory_service.get_formatted_memories_multi_user()` retrieves memories for every linked ID
4. results are deduplicated by `memory_id`

So if the same human talks to Nami from Discord and WhatsApp, memories can follow them instead of staying trapped in platform silos. Revolutionary stuff.

## How It Works

### 1. Memory Creation

```text
User: "I met Sarah at Example Lab and she prefers tea."
  ↓
MemoryExtractor parses memory candidates, concepts, and locations
  ↓
MemoryProcessor upserts Location("Example Lab")
  ↓
MemoryDb.add_memory(...) stores a KnowledgeUnit/EpisodicMemory
  ↓
Graph links are created:
    (Person)-[:IS_AUTHOR_OF]->(Memory)
    (Memory)-[:OCCURRED_AT]->(Location)   # when matched
    (Memory)-[:REFERS_TO_CONCEPT]->(CONCEPT)
```

### 2. Memory Retrieval

```text
New user message arrives
  ↓
ContextBuilder adds system prompt
  ↓
ContextBuilder injects user_info as slot 2
  ↓
resolve_canonical_users() expands linked identities
  ↓
MemoryService retrieves relevant memories for each linked user ID
  ↓
Duplicate hits are removed by memory_id
  ↓
Formatted memory block is added to prompt context
```

### 3. Relevance Scoring

Memory ranking combines:
- **Vector similarity**
- **Importance**
- **Recency / decay effects**
- **Hierarchy retrieval** (when enabled)

Default similarity threshold is `0.65`.

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

memory:
  embedding_model: all-MiniLM-L6-v2
  embedding_dimension: 384
  similarity_threshold: 0.65
  extraction_provider: ollama
  extraction_model: llama3.2
```

### Embedding Models

| Model | Dimensions | Size | Speed | Quality |
|-------|-----------:|------|-------|---------|
| `all-MiniLM-L6-v2` | 384 | 80MB | Fast | Good |
| `all-mpnet-base-v2` | 768 | 420MB | Medium | Better |
| `all-distilroberta-v1` | 768 | 290MB | Medium | Better |

**Recommendation:** `all-MiniLM-L6-v2` remains the default balance of speed and quality.

## API Usage

### Automatic Memory (Default)

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "ollama/llama3.2",
  "messages": [{"role": "user", "content": "Remember that I like mountain hikes."}],
  "user_id": "discord:123456789"
}'
```

With memory enabled, Nami will:
- retrieve relevant memories for that scoped identity
- inject them into the prompt
- extract new memories from the turn afterward

### Disable Memory

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "ollama/llama3.2",
  "messages": [{"role": "user", "content": "Hello"}],
  "user_id": "discord:123456789",
  "enable_memory": false
}'
```

### Per-User Memory

User IDs are **scoped**, not plain global names:

```bash
# Discord identity
{"user_id": "discord:123456789"}

# WhatsApp identity
{"user_id": "whatsapp:+15551234567"}
```

These are separate identities until linked with `SAME_PERSON_AS` (for example through `link_my_identity`).

### Per-Conversation Context

`conversation_id` is still tracked for chat history and request context:

```bash
curl ... -d '{
  "user_id": "discord:123456789",
  "conversation_id": "discord-general",
  ...
}'
```

## Viewing Memories

### Neo4j Browser

1. Open `http://localhost:7474`
2. Log in with your Neo4j credentials
3. Run queries such as:

```cypher
// People and authored memories
MATCH (p:Person)-[:IS_AUTHOR_OF]->(m)
RETURN p, m
LIMIT 25;

// Facts about third parties
MATCH (m)-[:IS_ABOUT]->(p:Person)
RETURN m, p
LIMIT 25;

// Memories with locations
MATCH (m)-[:OCCURRED_AT]->(l:Location)
RETURN m, l
LIMIT 25;

// Linked identities
MATCH (a:Person)-[:SAME_PERSON_AS]-(b:Person)
RETURN a, b
LIMIT 25;

// Find memories mentioning hiking
MATCH (m)
WHERE m.summary CONTAINS "hiking" OR m.statement CONTAINS "hiking" OR m.description CONTAINS "hiking"
RETURN m
LIMIT 25;
```

### Python Example

```python
from lib.memory_db import MemoryDb

memory_db = MemoryDb(
    neo4j_uri="bolt://localhost:7687",
    neo4j_user="neo4j",
    neo4j_pass="your_password",
)

results = await memory_db.search(
    query="hiking",
    filter_user_id="discord:123456789",
    top_k=5,
)

for memory, score in results:
    print(score, memory)
```

## Memory Management

### Clear All Memories

```cypher
MATCH (n) DETACH DELETE n;
```

### Clear One User's Authored Memories

This deletes memories authored by a specific person while leaving the `Person` node intact:

```cypher
MATCH (:Person {id: "discord:123456789"})-[:IS_AUTHOR_OF]->(m)
DETACH DELETE m;
```

### Remove an Identity Link

```cypher
MATCH (:Person {id: "discord:123456789"})-[r:SAME_PERSON_AS]-(:Person {id: "whatsapp:+15551234567"})
DELETE r;
```

### Export One User's Memories

```cypher
MATCH (p:Person {id: "discord:123456789"})-[:IS_AUTHOR_OF]->(m)
OPTIONAL MATCH (m)-[:OCCURRED_AT]->(l:Location)
RETURN p, m, l;
```

### Backup Database

```bash
docker stop neo4j

docker run --rm \
  -v neo4j_data:/data \
  -v $(pwd):/backup \
  ubuntu tar czf /backup/neo4j_backup.tar.gz /data

docker start neo4j
```

## Best Practices

1. **Use stable scoped IDs** such as `discord:123456789`
2. **Create third-party people explicitly** with `create_person` before storing facts with `remember_about_person`
3. **Link identities when you know they are the same human** so retrieval can span platforms
4. **Keep location names consistent** (`Example Lab` vs `example lab`) to improve location reuse
5. **Monitor Neo4j size and indexes** if the graph grows large

Recommended indexes:

```cypher
CREATE INDEX userIdIndex IF NOT EXISTS FOR (u:Person) ON (u.id);
CREATE INDEX locationIdIndex IF NOT EXISTS FOR (l:Location) ON (l.location_id);
```

## Troubleshooting

### No Memories Retrieved

- Verify Neo4j is reachable
- Check that `user_id` is scoped correctly
- Confirm memories exist for that `Person`
- Lower the similarity threshold if retrieval is too strict

```cypher
MATCH (p:Person)-[:IS_AUTHOR_OF]->(m)
RETURN p.id, count(m)
ORDER BY count(m) DESC;
```

### Cross-Platform Memories Not Appearing

Check whether the identities are linked:

```cypher
MATCH (a:Person {id: "discord:123456789"})-[:SAME_PERSON_AS*0..]-(b:Person)
RETURN DISTINCT b.id;
```

### Locations Not Linking

Inspect extracted locations and linked memories:

```cypher
MATCH (m)
OPTIONAL MATCH (m)-[:OCCURRED_AT]->(l:Location)
RETURN m, l
LIMIT 25;
```

## See Also

- [Architecture](../ARCHITECTURE.md)
- [Tools](../reference/tools.md)
- [Memory Flow Diagrams](../diagrams/02_memory_flow.md)
- Repository: https://github.com/oOHiyoriOo/nami_ai
