import ast
import logging
from app.config import settings
from typing import List, Dict

logger = logging.getLogger(__name__)

class CodeChunker:
    def __init__(self):
        self.chunk_size = settings.CHUNK_SIZE
        self.chunk_overlap = settings.CHUNK_OVERLAP

    def chunk_file(self, full_path: str, rel_path: str) -> List[Dict]:
        try:
            with open(full_path, "r", encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Error reading File during CHUNKING {full_path}: {e}")
            return []

        if full_path.endswith('.py'):
            return self._python_ast_chunk(content, rel_path)
        else:
            return self._fallback_chunk(content, rel_path)

    def _python_ast_chunk(self, content: str, rel_path: str) -> List[Dict]:
        chunks = []
        try:
            tree = ast.parse(content)
            lines = content.split('\n')
            index = 0
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start = node.lineno
                    end = node.end_lineno
                    chunk_text = "\n".join(lines[start-1:end])
                    chunks.append({
                        "file_path": rel_path,
                        "chunk_index": index,
                        "start_line": start,
                        "end_line": end,
                        "content": chunk_text
                    })
                    index += 1
            if not chunks:
                return self._fallback_chunk(content, rel_path)
            return chunks
        except SyntaxError:
            return self._fallback_chunk(content, rel_path)

    def _fallback_chunk(self, content: str, rel_path: str) -> List[Dict]:
        lines = content.split('\n')
        chunks = []
        index = 0
        i = 0
        while i < len(lines):
            chunk_lines = lines[i:i + self.chunk_size]
            chunk_text = "\n".join(chunk_lines)
            if chunk_text.strip():
                chunks.append({
                    "file_path": rel_path,
                    "chunk_index": index,
                    "start_line": i + 1,
                    "end_line": i + len(chunk_lines),
                    "content": chunk_text
                })
                index += 1
            i += (self.chunk_size - self.chunk_overlap)
        return chunks
