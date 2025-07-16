class KnowledgeUnit:
    def __getstate__(self):
        return self.to_dict()

    def __json__(self):
        import json
        return json.dumps(self.to_dict(), default=str)

    def __repr__(self):
        return f"KnowledgeUnit({self.to_dict()})"

    def __str__(self):
        return self.to_cypher()
    
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

    def to_cypher(self, alias="ku"):
        props = []
        for k, v in self.to_dict().items():
            if v is not None:
                if isinstance(v, list):
                    props.append(f'{k}: {v}')
                else:
                    props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:KnowledgeUnit {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()

    def __iter__(self):
        yield from self.to_dict().items()

    @staticmethod
    def list_to_dicts(units):
        """Convert a list of KnowledgeUnit objects to a list of dicts for JSON serialization."""
        return [u.to_dict() if hasattr(u, "to_dict") else u for u in units]
