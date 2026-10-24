import os
import re
import logging
from typing import List
from sqlalchemy.orm import Session
from app.config import settings
from app.agents.schemas import IssueAnalysis, RetrievedContextChunk
from app.agents.prompting import read_repo_file

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "should", "when", "from", "into", "have", "will", "would",
    "there", "their", "which", "where", "what", "issue", "currently", "behavior", "expected", "current",
    "return", "returns", "able", "also", "only", "must", "make", "does", "not", "are", "can", "add", "new",
}
_LEXICAL_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs", ".c", ".cpp",
    ".h", ".hpp", ".swift", ".scala", ".vue", ".svelte", ".sql", ".toml", ".cfg", ".ini", ".yaml", ".yml",
    ".json", ".md",
}
_MAX_FILE_CHARS = 6000


def _keywords(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    seen, out = set(), []
    for w in words:
        lw = w.lower()
        if lw in _STOPWORDS or lw in seen:
            continue
        seen.add(lw)
        out.append(lw)
    return out[:40]


def _best_window(lines: List[str], terms: List[str]) -> tuple[int, int]:
    """[start, end) line range of at most _MAX_FILE_CHARS with the most query-term hits.

    Large files used to be cut at the start, so the planner often saw only imports
    and never the code the issue was about.
    """
    hits = [sum(1 for t in terms if t in line.lower()) for line in lines] if terms else [0] * len(lines)
    sizes = [len(line) + 1 for line in lines]

    def end_for(start: int) -> int:
        used, end = 0, start
        while end < len(lines) and used + sizes[end] <= _MAX_FILE_CHARS:
            used += sizes[end]
            end += 1
        return max(end, start + 1)

    best = (0, end_for(0))
    best_score = sum(hits[best[0]:best[1]])
    for start in range(10, len(lines), 10):
        end = end_for(start)
        score = sum(hits[start:end])
        if score > best_score:
            best, best_score = (start, end), score
    return best


class ContextBuilder:
    def __init__(self, db: Session):
        self.db = db
        self.max_chunks = getattr(settings, "MAX_CONTEXT_CHUNKS", 10)
        self.retriever = None
        if db is None:
            # No index to search (e.g. benchmark runs): lexical retrieval only, and
            # no need to load the embedding model.
            return
        try:
            from app.services.code_retriever import CodeRetriever
            self.retriever = CodeRetriever(db)
        except Exception as e:
            # Embeddings are optional: without them we still have lexical retrieval.
            logger.warning(f"Semantic retrieval unavailable ({e}); using lexical retrieval only")
            self.retriever = None

    def build_context(self, repository_id: int, analysis: IssueAnalysis, workspace_path: str | None = None,
                      tracked_files: List[str] | None = None) -> List[RetrievedContextChunk]:
        search_query = f"{analysis.problem_summary} {analysis.current_behavior} {' '.join(analysis.affected_areas)}"
        tracked = set(tracked_files or [])
        terms = _keywords(search_query + " " + analysis.expected_behavior)
        contexts: List[RetrievedContextChunk] = []

        # 1. Files the analysis named explicitly (only if they really exist).
        if workspace_path:
            for area in analysis.affected_areas:
                path = area.strip().lstrip("./").replace("\\", "/")
                if path in tracked:
                    content = read_repo_file(workspace_path, path)
                    if content is not None:
                        contexts.append(self._chunk(path, content, 1.0, terms))

        # 2. Semantic retrieval over the indexed chunks.
        if self.retriever is not None:
            try:
                for res in self.retriever.search(repository_id, search_query, top_k=self.max_chunks):
                    if tracked and res["file_path"] not in tracked:
                        continue  # stale chunk from an older revision
                    contexts.append(self._chunk(res["file_path"], res["content"], 0.8))
            except Exception as e:
                logger.warning(f"Semantic retrieval failed ({e}); continuing with lexical retrieval")
                try:
                    self.db.rollback()
                except Exception:
                    pass

        # 3. Lexical retrieval over the actual checkout.
        if workspace_path and tracked:
            contexts.extend(self._lexical_search(workspace_path, tracked, terms))

        deduped, seen = [], set()
        for c in contexts:
            key = (c.file_path, c.content[:200])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(c)
        return deduped[: max(self.max_chunks, 1) * 2]

    @staticmethod
    def _chunk(path: str, content: str, score: float, terms: List[str] = ()) -> RetrievedContextChunk:
        """The whole file if small, else the window of _MAX_FILE_CHARS with the most query-term hits."""
        lines = content.split("\n")
        start, end = 0, len(lines)
        if len(content) > _MAX_FILE_CHARS:
            start, end = _best_window(lines, terms)
            content = "\n".join(lines[start:end])
            if start > 0:
                content = f"...(lines 1-{start} omitted)...\n" + content
            if end < len(lines):
                content += "\n...(truncated)..."
        return RetrievedContextChunk(file_path=path, start_line=start + 1, end_line=end, content=content, score=score)

    def _lexical_search(self, workspace_path: str, tracked: set, terms: List[str]) -> List[RetrievedContextChunk]:
        if not terms:
            return []
        scored = []
        for path in tracked:
            if os.path.splitext(path)[1].lower() not in _LEXICAL_EXTENSIONS:
                continue
            content = read_repo_file(workspace_path, path)
            if not content or len(content) > 200_000:
                continue
            lower_path, lower = path.lower(), content.lower()
            score = sum(3 for t in terms if t in lower_path) + sum(min(lower.count(t), 5) for t in terms)
            if score:
                scored.append((score, path, content))
        scored.sort(key=lambda s: (-s[0], s[1]))
        top = scored[: self.max_chunks]
        best = top[0][0] if top else 1
        return [self._chunk(path, content, round(score / best, 3), terms) for score, path, content in top]
