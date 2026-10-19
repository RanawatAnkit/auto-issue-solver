import pytest
import os
import uuid
import tempfile
from unittest.mock import patch, MagicMock
from app.services.repository_indexer import RepositoryIndexer
from app.services.code_retriever import CodeRetriever
from app.db.session import SessionLocal
from app.models.domain import Repository

@pytest.mark.postgres
@patch("app.services.repository_indexer.EmbeddingService")
@patch("app.services.code_retriever.EmbeddingService")
def test_indexer_and_retrieval(mock_retriever_emb, mock_indexer_emb):
    db = SessionLocal()
    # Unpack unique identifier safely mitigating UniqueViolation isolation leaks!
    repo = Repository(github_repo_id=str(uuid.uuid4().hex), owner="x", name="y", full_name=str(uuid.uuid4().hex), clone_url="z")
    db.add(repo)
    db.commit()

    mock_indexer_emb.return_value.generate_embeddings.return_value = [[0.1]*384]
    mock_retriever_emb.return_value.generate_embeddings.return_value = [[0.1]*384]

    indexer = RepositoryIndexer(db)
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "test.py"), "w") as f:
            f.write("def foo(): return 1")
            
        indexer.index_repository(repo.id, td)
        
    retriever = CodeRetriever(db)
    results = retriever.search(repo.id, "find foo")
    assert len(results) == 1
    assert results[0]["file_path"] == "test.py"
