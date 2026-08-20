from sqlalchemy.orm import Session
from app.models.domain import CodeChunk
from app.services.embedding_service import EmbeddingService

class CodeRetriever:
    def __init__(self, db: Session):
        self.db = db
        self.embedder = EmbeddingService()

    def search(self, repository_id: int, query: str, top_k: int = 10):
        # 1. Embed textual semantic query sequence
        query_emb = self.embedder.generate_embeddings([query])[0]
        
        # 2. Extract cosine thresholds relying on PgVector `<=>` logic
        chunks = self.db.query(CodeChunk)\
            .filter(CodeChunk.repository_id == repository_id)\
            .filter(CodeChunk.embedding != None)\
            .order_by(CodeChunk.embedding.cosine_distance(query_emb))\
            .limit(top_k).all()
            
        results = []
        for ch in chunks:
            results.append({
                "file_path": ch.file_path,
                "chunk_index": ch.chunk_index,
                "content": ch.content
            })
        return results
