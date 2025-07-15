/* 
erDiagram
    Person ||--o{ EpisodicMemory : IST_AUTOR_VON 
    Person ||--o{ KnowledgeUnit : IST_AUTOR_VON 
    Person ||--o{ ProceduralUnit : IST_AUTOR_VON 

    Person }o--o{ EpisodicMemory : NIMMT_TEIL_AN

    EpisodicMemory }|--|{ CONCEPT : "BEZIEHT_SICH_AUF_KONZEPT"
    KnowledgeUnit }|--|{ CONCEPT : "BEZIEHT_SICH_AUF_KONZEPT"
    ProceduralUnit }|--|{ CONCEPT : "BEZIEHT_SICH_AUF_KONZEPT"

    EpisodicMemory }o--|| Location : "FAND_STATT_AN_ORT"

    Person {
        int id PK "Discord user id"
        string name
        string nickname "nickname for the user"
    }

    EpisodicMemory {
        string id PK "uuid4()"
        string summary "Zusammenfassung"
        string description
        list_float summaryEmbeddingVector
        
        datetime timestampOccurred_approx "Datums schaetzung"
        string timeDescription "Zeitbeschreibung 'letzten sommer'"
        
        string emotionalValence "Hauptemotion der errinerung."
        float confidenceScore "How sure are you?"
        float vividnessScore "Wie klar und detailreich ist die errinerung"
        float emotionalIntensity "Wie stark ist die emotion?"

        string source "Information source"
        int authorUserId "Refers Person"
        datetime creationTimestamp
    }

    KnowledgeUnit {
        string id PK "uuid4()"
        string statement
        list_float summaryEmbeddingVector
        string type
        float confidenceScore
        string source "Information source"
        datetime creationTimestamp
        datetime validFrom
        datetime validUntil
        int authorUserId "Refers to Person"
    }

    ProceduralUnit {
        string id PK "uuid4()"
        string name
        string description
        text steps
        list_float summaryEmbeddingVector
        float proficiencyLevel
        float confidenceScore
        int authorUserId
        datetime creationTimestamp
    }

    CONCEPT {
        string id PK "uuid4()"
        string name UK "Unique name"
        string description
        list[string] keywords
    }

    Location {
        string id PK "uuid4()"
        string name
        string description
        string planeOfExistence "e.g. DnD, IRL, Minecraft"
    }

*/