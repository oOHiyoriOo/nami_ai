class Person:
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

    def to_cypher(self, alias="person"):
        props = []
        for k, v in self.to_dict().items():
            if v is not None:
                props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:Person {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()

    @staticmethod
    def list_to_dicts(persons):
        """Convert a list of Person objects to a list of dicts for JSON serialization."""
        return [p.to_dict() if hasattr(p, "to_dict") else p for p in persons]
