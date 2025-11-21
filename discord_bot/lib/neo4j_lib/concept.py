class Concept:
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

    def to_cypher(self, alias="concept"):
        props = []
        for k, v in self.to_dict().items():
            if v is not None:
                props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:CONCEPT {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()

    @staticmethod
    def list_to_dicts(concepts):
        """Convert a list of Concept objects to a list of dicts for JSON serialization."""
        return [c.to_dict() if hasattr(c, "to_dict") else c for c in concepts]
