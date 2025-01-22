from sentence_transformers import SentenceTransformer
import faiss
import numpy as np

class VectorDatabase:
    def __init__(self, model_name='all-MiniLM-L6-v2', dimension=384):
        """
        Initialisiert die Vektordatenbank mit SentenceTransformers und FAISS.
        :param model_name: Name des SentenceTransformers-Modells.
        :param dimension: Dimension des Embeddings (abhängig vom Modell).
        """
        # SentenceTransformers-Modell auf CPU laden
        self.model = SentenceTransformer(model_name, device='cpu')

        # FAISS-Index initialisieren
        self.dimension = dimension
        self.index = faiss.IndexFlatL2(dimension)  # L2-Norm (euclidische Distanz)

        # Textspeicher für Abruf
        self.texts = []

    def add_texts(self, texts):
        """
        Fügt Texte zur Datenbank hinzu und speichert deren Embeddings.
        :param texts: Liste von Texten (Strings).
        """
        # Embeddings erstellen
        embeddings = np.array(self.model.encode(texts), dtype=np.float32)

        # Embeddings dem FAISS-Index hinzufügen
        self.index.add(embeddings)

        # Texte speichern
        self.texts.extend(texts)

    def search(self, query, top_k=5):
        """
        Sucht relevante Texte basierend auf einer Abfrage.
        :param query: Der Abfragetext.
        :param top_k: Anzahl der ähnlichen Ergebnisse, die zurückgegeben werden.
        :return: Liste von (Text, Ähnlichkeit)-Tupeln.
        """
        # Query-Embedding erstellen
        query_embedding = np.array([self.model.encode(query)], dtype=np.float32)

        # Suche im FAISS-Index
        distances, indices = self.index.search(query_embedding, top_k)

        # Ergebnisse sammeln
        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.texts):  # Sicherheitscheck
                results.append((self.texts[idx], distance))

        return results

    def get_total_entries(self):
        """
        Gibt die Gesamtanzahl der gespeicherten Einträge zurück.
        :return: Anzahl der gespeicherten Einträge.
        """
        return self.index.ntotal


# Beispielverwendung
if __name__ == "__main__":
    # Instanz der Klasse
    db = VectorDatabase()

    # Texte hinzufügen
    db.add_texts(["Ein Apfel ist ein Obst", "Obst ist gesund", "Künstliche Intelligenz ist spannend"])

    # Abfrage durchführen
    query = "Was ist ein gesundes Obst?"
    results = db.search(query, top_k=2)

    # Ergebnisse anzeigen
    print(f"Gefundene Texte für die Abfrage '{query}':")
    for text, score in results:
        print(f"- {text} (Ähnlichkeit: {score:.4f})")

    # Gesamtanzahl der Einträge
    print(f"Gespeicherte Einträge: {db.get_total_entries()}")
