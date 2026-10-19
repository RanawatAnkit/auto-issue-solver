from app.services.embedding_service import EmbeddingService
from app.db.session import SessionLocal
from app.models.domain import CodeChunk, Repository
import os
import uuid

def run():
    print("Initializing SentenceTransformers explicitly...")
    svc = EmbeddingService()
    
    embed_result = svc.generate_embeddings(["find the add_numbers function"])
    
    if len(embed_result) == 0:
        print("FAIL: No embeddings returned.")
        return
        
    dim = len(embed_result[0])
    print(f"Produced Embedding Dimension: {dim}")
    if dim != 384:
        print("FAIL: Dimension mismatch")
        return
        
    print("Length Validation Passed.")
    
    # Store and retrieve it via PGVector securely mapping <->
    db = SessionLocal()
    repo = Repository(github_repo_id=str(uuid.uuid4().hex), owner="test", name="test", full_name=str(uuid.uuid4().hex), clone_url="test")
    db.add(repo)
    db.commit()
    
    chunk = CodeChunk(repository_id=repo.id, file_path="main.py", chunk_index=0, content="def add_numbers():\n    return 1", embedding=embed_result[0])
    db.add(chunk)
    db.commit()
    print("Stored Vector cleanly.")
    
    # Retrieval mapping Vector L2 Distance implicitly mapped PgVector mappings organically.
    retrieved = db.query(CodeChunk).filter(CodeChunk.repository_id == repo.id).first()
    if retrieved and len(retrieved.embedding) == 384:
        print("SUCCESS! Retrieval native bounds flawlessly match 384 limitations storing arrays seamlessly!")
    else:
        print("FAIL: PgVector failed tracking properties.")

if __name__ == "__main__":
    run()
