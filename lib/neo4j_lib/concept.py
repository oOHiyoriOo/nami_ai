class Concept:
    def __init__(self, id: str, name: str, description: str = None, keywords=None, **args):
        self.id = id
        self.name = name
        self.description = description
        self.keywords = keywords or []

    def to_dict(self):
        return self.__dict__

    def to_cypher(self, alias="concept"):
        props = []
        for k, v in self.to_dict().items():
            if v is not None:
                props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:CONCEPT {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()
