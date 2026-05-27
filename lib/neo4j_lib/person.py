class Person:
    """Represents a person node in the Neo4j memory graph.

    Person nodes serve as anchors for linking memories, knowledge, and concepts
    to specific individuals (e.g., users, authors). They enable the memory graph
    to track who created or is associated with each memory entity.
    """

    def __init__(self, id: int, name: str, nickname: str = None, **args):
        self.id = id
        self.name = name
        self.nickname = nickname

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
        return "Person"

    def get_properties(self):
        """Returns properties as a dict for parameterized queries."""
        return {k: v for k, v in self.to_dict().items() if v is not None}

    def __str__(self):
        import json
        return f"Person({json.dumps(self.to_dict())})"

    @staticmethod
    def list_to_dicts(persons):
        """Convert a list of Person objects to a list of dicts for JSON serialization."""
        return [p.to_dict() if hasattr(p, "to_dict") else p for p in persons]
