import os
import tempfile
from app.services.code_chunker import CodeChunker

def test_ast_python_chunking():
    chunker = CodeChunker()
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "dummy.py")
        content = "def foo():\n    pass\nclass Bar:\n    pass"
        with open(src, "w") as f:
            f.write(content)
            
        chunks = chunker.chunk_file(src, "dummy.py")
        assert len(chunks) == 2
        assert "def foo():" in chunks[0]["content"]

def test_fallback_line_chunking():
    chunker = CodeChunker()
    chunker.chunk_size = 2
    chunker.chunk_overlap = 0
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "main.js")
        with open(src, "w") as f:
            f.write("1\n2\n3\n4\n")
        chunks = chunker.chunk_file(src, "main.js")
        assert len(chunks) == 2
