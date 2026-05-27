class Location:
    """Represents a physical or conceptual location node in the Neo4j memory graph.

    Location nodes serve as geo-tags for episodic memories, allowing them to be
    linked to specific places and enabling spatial and contextual queries across
    the memory graph.
    """

    def __init__(self, id: str, name: str, description: str = None, planeOfExistence: str = None, **args):
        self.id = id
        self.name = name
        self.description = description
        self.planeOfExistence = planeOfExistence

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
        return "Location"

    def get_properties(self):
        """Returns properties as a dict for parameterized queries."""
        return {k: v for k, v in self.to_dict().items() if v is not None}

    def to_cypher(self, alias="loc"):
        props = []
        for k, v in self.get_properties().items():
            props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:Location {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()

    @staticmethod
    def list_to_dicts(locations):
        """Convert a list of Location objects to a list of dicts for JSON serialization."""
        return [l.to_dict() if hasattr(l, "to_dict") else l for l in locations]
