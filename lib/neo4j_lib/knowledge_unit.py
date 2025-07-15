
class KnowledgeUnit:
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
        return self.__dict__

    def to_cypher(self, alias="ku"):
        props = []
        for k, v in self.to_dict().items():
            if v is not None:
                props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:KnowledgeUnit {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()
