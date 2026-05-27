class ProceduralUnit:
    """Represents a learned procedure or skill in the Neo4j memory graph.

    ProceduralUnits capture step-by-step instructions, workflows, or
    task-execution patterns that the agent can follow or perform. They track
    proficiency level and can be associated with concepts for skill organization.
    """

    def __getstate__(self):
        return self.to_dict()

    def __json__(self):
        import json
        return json.dumps(self.to_dict(), default=str)

    def __repr__(self):
        return f"ProceduralUnit({self.to_dict()})"

    def __str__(self):
        import json
        return f"ProceduralUnit({json.dumps(self.to_dict(), default=str)})"

    def to_json(self):
        import json
        return json.dumps(self.to_dict(), default=str)

    def __init__(self, id: str, name: str, description: str = None, steps: str = None,
                 summaryEmbeddingVector=None, proficiencyLevel=None, confidenceScore=None,
                 authorUserId=None, creationTimestamp=None, concepts=None, **args):
        self.id = id
        self.name = name
        self.description = description
        self.steps = steps
        self.summaryEmbeddingVector = summaryEmbeddingVector or []
        self.proficiencyLevel = proficiencyLevel
        self.confidenceScore = confidenceScore
        self.authorUserId = authorUserId
        self.creationTimestamp = creationTimestamp
        self.concepts = concepts or []

    def to_dict(self):
        return {
            k: v for k, v in self.__dict__.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }

    def __iter__(self):
        yield from self.to_dict().items()

    def get_label(self):
        """Returns the node label for this entity."""
        return "ProceduralUnit"

    def get_properties(self):
        """Returns properties as a dict for parameterized queries."""
        return {k: v for k, v in self.to_dict().items() if v is not None}
    
    @staticmethod
    def list_to_dicts(units):
        """Convert a list of ProceduralUnit objects to a list of dicts for JSON serialization."""
        return [u.to_dict() if hasattr(u, "to_dict") else u for u in units]
