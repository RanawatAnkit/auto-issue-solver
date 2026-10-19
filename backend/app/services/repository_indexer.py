import logging
import os
from sqlalchemy.orm import Session
from app.models.domain import CodeChunk, Repository
from app.services.repository_scanner import RepositoryScanner
from app.services.code_chunker import CodeChunker
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

class RepositoryIndexer:
    def __init__(self, db: Session):
        self.db = db
        self.scanner = RepositoryScanner()
        self.chunker = CodeChunker()
        self.embedder = EmbeddingService()

    def index_repository(self, repository_id: int, workspace_path: str) -> int:
        """Re-index a checkout and return the number of chunks stored."""
        logger.info(f"Indexing repository {repository_id} from {workspace_path}")

        files = self.scanner.scan(workspace_path)
        logger.info(f"Scanner found {len(files)} indexable files")

        raw_chunks = []
        for f in files:
            full_path = os.path.join(workspace_path, f)
            raw_chunks.extend(self.chunker.chunk_file(full_path, f))

        # Embed before touching the existing index so a failure leaves the old one intact.
        embeddings = self.embedder.generate_embeddings([c["content"] for c in raw_chunks]) if raw_chunks else []

        self.db.query(CodeChunk).filter(CodeChunk.repository_id == repository_id).delete()
        self.db.add_all([
            CodeChunk(
                repository_id=repository_id,
                file_path=c["file_path"],
                chunk_index=c["chunk_index"],
                content=c["content"],
                embedding=emb
            )
            for c, emb in zip(raw_chunks, embeddings)
        ])
        self.db.commit()
        logger.info(f"Stored {len(raw_chunks)} chunks for repository {repository_id}")
        return len(raw_chunks)
