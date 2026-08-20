import logging
from typing import List
from sqlalchemy.orm import Session
from app.config import settings
from app.services.code_retriever import CodeRetriever
from app.agents.schemas import IssueAnalysis, RetrievedContextChunk

logger = logging.getLogger(__name__)

class ContextBuilder:
    def __init__(self, db: Session):
        self.db = db
        self.retriever = CodeRetriever(db)
        self.max_chunks = getattr(settings, "MAX_CONTEXT_CHUNKS", 10)

    def build_context(self, repository_id: int, analysis: IssueAnalysis) -> List[RetrievedContextChunk]:
        search_query = f"{analysis.problem_summary} {analysis.current_behavior} {' '.join(analysis.affected_areas)}"
        
        results = self.retriever.search(repository_id, search_query, top_k=self.max_chunks)
        contexts = []
        for res in results:
            contexts.append(
                RetrievedContextChunk(
                    file_path=res["file_path"],
                    start_line=1,
                    end_line=len(res["content"].split('\n')),
                    content=res["content"],
                    score=1.0
                )
            )
        
        return contexts
