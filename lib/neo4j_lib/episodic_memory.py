class EpisodicMemory:
    def __init__(self, id: str, summary: str, description: str = None, summaryEmbeddingVector=None,
                 timestampOccurred_approx=None, timeDescription=None, emotionalValence=None,
                 confidenceScore=None, vividnessScore=None, emotionalIntensity=None,
                 source=None, authorUserId=None, creationTimestamp=None, location=None, concepts=None, **args):
        self.id = id
        self.summary = summary
        self.description = description
        self.summaryEmbeddingVector = summaryEmbeddingVector or []
        self.timestampOccurred_approx = timestampOccurred_approx
        self.timeDescription = timeDescription
        self.emotionalValence = emotionalValence
        self.confidenceScore = confidenceScore
        self.vividnessScore = vividnessScore
        self.emotionalIntensity = emotionalIntensity
        self.source = source
        self.authorUserId = authorUserId
        self.creationTimestamp = creationTimestamp
        self.location = location
        self.concepts = concepts or []

    def to_dict(self):
        return self.__dict__

    def to_cypher(self, alias="em"):
        props = []
        for k, v in self.to_dict().items():
            if v is not None:
                props.append(f'{k}: "{v}"')
        props_str = ", ".join(props)
        return f'({alias}:EpisodicMemory {{{props_str}}})'

    def __str__(self):
        return self.to_cypher()
