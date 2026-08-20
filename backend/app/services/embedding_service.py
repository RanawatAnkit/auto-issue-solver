import logging
import openai
from typing import List
from app.config import settings

logger = logging.getLogger(__name__)

class EmbeddingService:
    def __init__(self):
        self.provider = getattr(settings, "EMBEDDING_PROVIDER", "local").lower()
        self.model = getattr(settings, "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        self.batch_size = getattr(settings, "EMBEDDING_BATCH_SIZE", 100)
        
        self._local_model = None
        if self.provider == "local":
            try:
                from sentence_transformers import SentenceTransformer
                self._local_model = SentenceTransformer(self.model)
            except ImportError:
                raise ImportError("sentence-transformers is not installed. Required for local embedding provider.")
        elif self.provider == "openai":
            if not settings.OPENAI_API_KEY:
                raise ValueError("Configuration Error: OPENAI_API_KEY not configured. Required for real Embedding mappings.")
        else:
            raise NotImplementedError(f"Embedding Provider {self.provider} not supported.")

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
            
        if self.provider == "local":
            embeddings = self._local_model.encode(texts, batch_size=self.batch_size, show_progress_bar=False)
            result = embeddings.tolist()
            if result and len(result[0]) != 384:
                raise ValueError(f"CRITICAL DIMENSIONALITY MISMATCH: Expected 384 vector length, produced {len(result[0])}")
            return result
        elif self.provider == "openai":
            import openai
            client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
            
            result = []
            for i in range(0, len(texts), self.batch_size):
                batch = texts[i:i + self.batch_size]
                try:
                    response = client.embeddings.create(input=batch, model=self.model)
                    out_batch = [data.embedding for data in response.data]
                    
                    if out_batch and len(out_batch[0]) != 1536:
                        raise ValueError(f"CRITICAL DIMENSIONALITY MISMATCH: Expected 1536 vector length, produced {len(out_batch[0])}")
                        
                    result.extend(out_batch)
                except Exception as e:
                    logger.error(f"Failed openAI embedding chunk iteration: {e}")
                    raise
            return result
        else:
            raise NotImplementedError(f"Embedding Provider {self.provider} not supported.")
