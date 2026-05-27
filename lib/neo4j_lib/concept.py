class Concept:
    """Represents a concept or tag node in the Neo4j memory graph.

    Concept nodes are used to categorize and cross-reference memory entities
    (EpisodicMemory, KnowledgeUnit, ProceduralUnit) via relationships. They
    enable semantic organization and keyword-based retrieval across the graph.
    """

    def __init__(self, id: str, name: str, description: str = None, keywords=None, **args):
        self.id = id
        self.name = name
        self.description = description
        self.keywords = keywords or []

    def to_dict(self):
        return {
            k: v for k, v in self.__dict__.items()
            if isinstance(v, (str, int, float, bool, list, dict, type(None)))
        }

    def __iter__(self):
        yield from self.to_dict().items()

    def __json__(self):
        import json
        return json.dumps(self.to_dict(), default=str)

    def get_label(self):
        """Returns the node label for this entity."""
        return "CONCEPT"

    def get_properties(self):
        """Returns properties as a dict for parameterized queries."""
        return {k: v for k, v in self.to_dict().items() if v is not None}

    def __str__(self):
        import json
        return f"Concept({json.dumps(self.to_dict())})"

    @staticmethod
    def list_to_dicts(concepts):
        """Convert a list of Concept objects to a list of dicts for JSON serialization."""
        return [c.to_dict() if hasattr(c, "to_dict") else c for c in concepts]
