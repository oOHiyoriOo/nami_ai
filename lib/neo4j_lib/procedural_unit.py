class ProceduralUnit:
    def __getstate__(self):
        return self.to_dict()

    def __json__(self):
        import json
        return json.dumps(self.to_dict(), default=str)

    def __repr__(self):
        return f"ProceduralUnit({self.to_dict()})"

    def __str__(self):
        return self.to_cypher()
    
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

    def to_cypher(self, alias="pu"):
        props = []
        for k, v in self.to_dict().items():
            if v is not None:
                if isinstance(v, list):
                    props.append(f'{k}: {v}')
                else:
                    props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:ProceduralUnit {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()
    
    @staticmethod
    def list_to_dicts(units):
        """Convert a list of ProceduralUnit objects to a list of dicts for JSON serialization."""
        return [u.to_dict() if hasattr(u, "to_dict") else u for u in units]
