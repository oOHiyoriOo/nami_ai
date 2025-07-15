class Person:
    def __init__(self, id: int, name: str, nickname: str = None, **args):
        self.id = id
        self.name = name
        self.nickname = nickname

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "nickname": self.nickname
        }

    def to_cypher(self, alias="person"):
        props = []
        for k, v in self.to_dict().items():
            if v is not None:
                props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:Person {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()
