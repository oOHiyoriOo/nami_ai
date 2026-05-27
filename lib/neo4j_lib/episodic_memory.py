class EpisodicMemory:
    """Represents a specific event or experience memory in the Neo4j memory graph.

    Episodic memories capture personal experiences tied to a particular time,
    place, and emotional context. They form the autobiographical knowledge of
    the AI agent and can be associated with concepts for semantic retrieval.
    """

    def __getstate__(self):
        return self.to_dict()

    def __repr__(self):
        return f"EpisodicMemory({self.to_dict()})"

    def __str__(self):
        import json
        return f"EpisodicMemory({json.dumps(self.to_dict(), default=str)})"
    
    def to_json(self):
        import json
        return json.dumps(self.to_dict(), default=str)
    
    def __init__(self, id: str, summary: str, description: str = None, summaryEmbeddingVector=None,
                 timestampOccurred_approx=None, timeDescription=None, emotionalValence=None,
                 confidenceScore=None, vividnessScore=None, emotionalIntensity=None,
                 source=None, authorUserId=None, creationTimestamp=None, location=None, concepts=None, **args):
        self.id = id
        self.summary = summary
        self.description = description
        self.summaryEmbeddingVector = summaryEmbeddingVector or []
        self.timestampOccurred_approx = timestampOccurred_approx
        self.timeDescription = timeDescription
        self.emotionalValence = emotionalValence
        self.confidenceScore = confidenceScore
        self.vividnessScore = vividnessScore
        self.emotionalIntensity = emotionalIntensity
        self.source = source
        self.authorUserId = authorUserId
        self.creationTimestamp = creationTimestamp
        self.location = location
        self.concepts = concepts or []

    def to_dict(self):
        # Only include serializable fields
        return {
            k: v for k, v in self.__dict__.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }

    def get_label(self):
        """Returns the node label for this entity."""
        return "EpisodicMemory"

    def get_properties(self):
        """Returns properties as a dict for parameterized queries."""
        return {k: v for k, v in self.to_dict().items() if v is not None}

    def __iter__(self):
        # Allows json.dumps to serialize this object
        yield from self.to_dict().items()

    def __json__(self):
        import json
        return json.dumps(self.to_dict(), default=str)

    @staticmethod
    def list_to_dicts(memories):
        """Convert a list of EpisodicMemory objects to a list of dicts for JSON serialization."""
        return [m.to_dict() if hasattr(m, "to_dict") else m for m in memories]
