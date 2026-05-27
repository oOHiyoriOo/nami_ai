class KnowledgeUnit:
    """Represents a factual statement or piece of knowledge in the Neo4j memory graph.

    KnowledgeUnits store declarative facts, assertions, or information that can
    be independently verified. They support optional temporal validity windows
    via validFrom/validUntil and can be linked to concepts for categorization.
    """

    def __getstate__(self):
        return self.to_dict()

    def __json__(self):
        import json
        return json.dumps(self.to_dict(), default=str)

    def __repr__(self):
        return f"KnowledgeUnit({self.to_dict()})"

    def __str__(self):
        import json
        return f"KnowledgeUnit({json.dumps(self.to_dict(), default=str)})"

    def to_json(self):
        import json
        return json.dumps(self.to_dict(), default=str)

    def __init__(self, id: str, statement: str, summaryEmbeddingVector=None, type=None,
                 confidenceScore=None, source=None, creationTimestamp=None, validFrom=None,
                 validUntil=None, authorUserId=None, concepts=None, **args):
        self.id = id
        self.statement = statement
        self.summaryEmbeddingVector = summaryEmbeddingVector or []
        self.type = type
        self.confidenceScore = confidenceScore
        self.source = source
        self.creationTimestamp = creationTimestamp
        self.validFrom = validFrom
        self.validUntil = validUntil
        self.authorUserId = authorUserId
        self.concepts = concepts or []

    def to_dict(self):
        return {
            k: v for k, v in self.__dict__.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }

    def get_label(self):
        """Returns the node label for this entity."""
        return "KnowledgeUnit"

    def get_properties(self):
        """Returns properties as a dict for parameterized queries."""
        return {k: v for k, v in self.to_dict().items() if v is not None}

    def __iter__(self):
        yield from self.to_dict().items()

    @staticmethod
    def list_to_dicts(units):
        """Convert a list of KnowledgeUnit objects to a list of dicts for JSON serialization."""
        return [u.to_dict() if hasattr(u, "to_dict") else u for u in units]
