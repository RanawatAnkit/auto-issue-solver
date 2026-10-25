import os
import re
import keyword
import builtins
import logging
from dataclasses import dataclass, field
from typing import List
from sqlalchemy.orm import Session
from app.config import settings
from app.agents.schemas import IssueAnalysis, RetrievedContextChunk, CodeLocation
from app.agents.prompting import read_repo_file
from app.agents.planner import reconcile_repo_path, is_test_file

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
_MAX_SYMBOL_CHUNKS_PER_FILE = 3
_SEED_LIMIT = 12
# A name matching more symbols than this is too ambiguous to count as a hint on its own.
_MAX_HINT_MATCHES = 3

_TRACEBACK_RE = re.compile(r'File "([^"\n]+)", line (\d+)(?:, in ([\w<>.]+))?')
_PATH_RE = re.compile(
    r"(?<![\w/])((?:[\w.-]+/)*[\w-]+\.(?:py|pyi|js|jsx|ts|tsx|java|go|rb|rs|php|cs|cpp|cc|c|h|hpp|kt|scala|swift))\b")
_BACKTICK_RE = re.compile(r"`([^`\n]{1,200})`")
_QUALIFIED_RE = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_CAMEL_RE = re.compile(r"\b[A-Z][a-z0-9]+[A-Z]\w*\b")
_CAPITALIZED_RE = re.compile(r"\b[A-Z][a-z]{3,}\w*\b")
_SNAKE_RE = re.compile(r"\b[a-z_][a-z0-9]*_[a-z0-9_]+\b")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_ROUTE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[\w/{}<>:.\-]*)")
# '@router.get("/x")', '@app.route("/x", methods=["POST"])', '@bp.api_route(path="/x")'
_ROUTE_DECORATOR_RE = re.compile(r"\.(get|post|put|patch|delete|route|api_route)\(\s*(?:path\s*=\s*)?['\"]([^'\"]*)['\"]")
_ROUTER_PREFIX_RE = re.compile(r"prefix\s*=\s*['\"]([^'\"]*)['\"]")
_IGNORED_NAMES = set(keyword.kwlist) | set(dir(builtins)) | {"self", "cls"}


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


@dataclass
class IssueHints:
    """Code references found in the issue text without any LLM call."""
    frames: List[tuple] = field(default_factory=list)       # (path as written, line, function)
    paths: List[str] = field(default_factory=list)
    routes: List[tuple] = field(default_factory=list)       # (HTTP method, path), e.g. ("GET", "/tasks")
    identifiers: List[tuple] = field(default_factory=list)  # (name, where it was found)


def extract_hints(text: str, max_identifiers: int = 40) -> IssueHints:
    text = text or ""
    hints = IssueHints()
    for m in _TRACEBACK_RE.finditer(text):
        hints.frames.append((m.group(1), int(m.group(2)), m.group(3) or ""))
    for m in _PATH_RE.finditer(text):
        if m.group(1) not in hints.paths:
            hints.paths.append(m.group(1))
    for m in _ROUTE_RE.finditer(text):
        route = (m.group(1), m.group(2).rstrip(".,:;"))
        if route not in hints.routes:
            hints.routes.append(route)

    seen = set()
    def add(name: str, origin: str):
        name = name.strip(".")
        if (not name or name in seen or name.split(".")[-1] in _IGNORED_NAMES or len(name) < 3
                or name in hints.paths or any(name.endswith("." + p.rsplit(".", 1)[-1]) for p in hints.paths)):
            return
        seen.add(name)
        hints.identifiers.append((name, origin))

    # Most specific sources first: code spans, qualified names, calls, then naming conventions.
    for span in _BACKTICK_RE.findall(text):
        for name in _IDENT_RE.findall(span):
            add(name, "code span")
    for name in _QUALIFIED_RE.findall(text):
        add(name, "qualified name")
    for name in _CALL_RE.findall(text):
        add(name, "call")
    for regex, origin in ((_CAMEL_RE, "class-like name"), (_SNAKE_RE, "identifier"), (_CAPITALIZED_RE, "capitalized name")):
        for name in regex.findall(text):
            add(name, origin)
    hints.identifiers = hints.identifiers[:max_identifiers]
    return hints


def _route_key(path: str) -> tuple:
    segments = [s for s in path.split("?")[0].strip("/").split("/") if s]
    return tuple("{}" if s[:1] in "{<" else s.lower() for s in segments)


def route_handlers(index, method: str, path: str) -> list:
    """Functions whose route decorator (plus any router prefix in the module) matches METHOD /path."""
    want, verb_wanted, found, prefixes = _route_key(path), method.lower(), [], {}
    for sym in index.symbols:
        if sym.kind == "class":
            continue
        for dec in sym.decorators:
            m = _ROUTE_DECORATOR_RE.search(dec)
            if not m:
                continue
            verb, sub = m.group(1), m.group(2)
            if verb in ("route", "api_route"):
                methods = [x.lower() for x in re.findall(r"['\"](GET|POST|PUT|PATCH|DELETE)['\"]", dec, re.I)]
                if verb_wanted not in (methods or ["get"]):
                    continue
            elif verb != verb_wanted:
                continue
            if sym.file_path not in prefixes:
                module_text = "\n".join(index.lines(sym.file_path) or [])
                prefixes[sym.file_path] = _ROUTER_PREFIX_RE.findall(module_text) or [""]
            if any(_route_key(f"{pre}/{sub}") == want for pre in prefixes[sym.file_path]) or _route_key(sub) == want:
                found.append(sym)
                break
    return found


def seed_locations(index, hints: IssueHints, limit: int = _SEED_LIMIT) -> List[CodeLocation]:
    """Resolve issue hints to verified code locations using the symbol index."""
    found: dict = {}

    def add(path: str, sym, evidence: str):
        key = (path, sym.qualname if sym else "")
        if key in found:
            if evidence not in found[key].evidence:
                found[key].evidence.append(evidence)
            return
        if sym is not None:
            loc = CodeLocation(file_path=path, symbol=sym.qualname, kind=sym.kind, start_line=sym.start_line,
                               end_line=sym.end_line, snippet=index.snippet(sym, max_lines=40), evidence=[evidence])
        else:
            n = index.line_count(path) or 1
            loc = CodeLocation(file_path=path, kind="file", start_line=1, end_line=n,
                               snippet=index.read_lines(path, 1, n, max_lines=40), evidence=[evidence])
        found[key] = loc

    hinted_files = []
    for raw, line, func in hints.frames:
        path = reconcile_repo_path(raw, index.files)
        if not path or not index.has_file(path):
            continue
        hinted_files.append(path)
        sym = index.enclosing_symbol(path, line) if line <= (index.line_count(path) or 0) else None
        if func and (sym is None or sym.name != func):
            alternatives = index.resolve(func, path)
            if len(alternatives) == 1:
                sym = alternatives[0]
        add(path, sym, f"traceback frame {raw}:{line}" + (f" in {func}" if func else ""))

    for raw in hints.paths:
        path = reconcile_repo_path(raw, index.files)
        if path and index.has_file(path) and path not in hinted_files:
            hinted_files.append(path)

    for method, path in hints.routes:
        for sym in route_handlers(index, method, path)[:_MAX_HINT_MATCHES]:
            add(sym.file_path, sym, f"handles `{method} {path}` named in the issue")

    for name, origin in hints.identifiers:
        matches = index.resolve(name) if "." in name else index.find_class(name) + index.find_function(name)
        evidence = f"issue mentions `{name}` ({origin})"
        if not matches and "." not in name and origin != "capitalized name":
            # e.g. `page_size`: the functions that already take it are where the change lands.
            matches = [s for s in index.symbols if name in s.params]
            evidence = f"issue mentions `{name}`, an existing parameter"
        if len(matches) > _MAX_HINT_MATCHES:
            matches = [m for m in matches if m.file_path in hinted_files][:_MAX_HINT_MATCHES]
        for m in matches:
            add(m.file_path, m, evidence)

    # Files named in the issue with nothing more specific found in them.
    for path in hinted_files:
        if not any(k[0] == path for k in found):
            add(path, None, "file named in the issue")

    ordered = list(found.values())
    # Traceback frames keep their position; otherwise prefer non-test code.
    ordered.sort(key=lambda loc: (not any(e.startswith(("traceback", "handles `")) for e in loc.evidence),
                                  is_test_file(loc.file_path)))
    return ordered[:limit]


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
        self.seed_locations: List[CodeLocation] = []
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
                      tracked_files: List[str] | None = None, symbol_index=None,
                      issue_text: str = "") -> List[RetrievedContextChunk]:
        search_query = f"{analysis.problem_summary} {analysis.current_behavior} {' '.join(analysis.affected_areas)}"
        terms = _keywords(search_query + " " + analysis.expected_behavior)
        tracked = set(tracked_files or [])
        terms = _keywords(search_query + " " + analysis.expected_behavior)
        contexts: List[RetrievedContextChunk] = []

        # 0. Code the issue points at directly (tracebacks, paths, identifiers), verified by the symbol index.
        self.seed_locations = []
        if symbol_index is not None:
            try:
                hints = extract_hints(f"{issue_text}\n{' '.join(analysis.affected_areas)}")
                self.seed_locations = seed_locations(symbol_index, hints)
            except Exception as e:
                logger.warning(f"Hint lookup failed ({e}); continuing without it")
            for loc in self.seed_locations:
                content = symbol_index.read_lines(loc.file_path, loc.start_line, loc.end_line,
                                                  max_lines=200, numbered=False)
                contexts.append(RetrievedContextChunk(
                    file_path=loc.file_path, start_line=loc.start_line, end_line=loc.end_line,
                    content=content, score=0.95, symbol=loc.symbol, source="hint"))

        # 1. Files the analysis named explicitly (only if they really exist).
        if workspace_path:
            for area in analysis.affected_areas:
                path = area.strip().lstrip("./").replace("\\", "/")
                if path in tracked:
                    content = read_repo_file(workspace_path, path)
                    if content is not None:
                        contexts.extend(self._file_chunks(path, content, 1.0, terms, symbol_index, "analysis"))

        # 2. Semantic retrieval over the indexed chunks.
        if self.retriever is not None:
            try:
                for res in self.retriever.search(repository_id, search_query, top_k=self.max_chunks):
                    if tracked and res["file_path"] not in tracked:
                        continue  # stale chunk from an older revision
                    chunk = self._locate(workspace_path, res["file_path"], res["content"], 0.8)
                    if chunk is not None:
                        contexts.append(chunk)
            except Exception as e:
                logger.warning(f"Semantic retrieval failed ({e}); continuing with lexical retrieval")
                try:
                    self.db.rollback()
                except Exception:
                    pass

        # 3. Lexical retrieval over the actual checkout.
        if workspace_path and tracked:
            contexts.extend(self._lexical_search(workspace_path, tracked, terms, symbol_index))

        deduped, seen = [], set()
        for c in contexts:
            key = (c.file_path, c.content[:200])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(c)
        return deduped[: max(self.max_chunks, 1) * 2]

    @staticmethod
    def _chunk(path: str, content: str, score: float, terms: List[str] = (), source: str = "") -> RetrievedContextChunk:
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
        return RetrievedContextChunk(file_path=path, start_line=start + 1, end_line=end, content=content, score=score,
                                     source=source)

    @staticmethod
    def _file_chunks(path: str, content: str, score: float, terms: List[str], symbol_index,
                     source: str) -> List[RetrievedContextChunk]:
        """A small file whole; a large indexed file as its most relevant symbols with real line ranges."""
        if len(content) <= _MAX_FILE_CHARS or symbol_index is None or not symbol_index.symbols_in_file(path):
            return [ContextBuilder._chunk(path, content, score, terms, source)]
        lines = content.split("\n")
        ranked = []
        for sym in symbol_index.symbols_in_file(path):
            if symbol_index.is_large_class(sym):
                continue  # its methods are ranked individually
            text = "\n".join(lines[sym.start_line - 1:sym.end_line]).lower()
            s = sum(3 for t in terms if t in sym.qualname.lower()) + sum(min(text.count(t), 5) for t in terms)
            if s:
                ranked.append((s, sym, text))
        if not ranked:
            return [ContextBuilder._chunk(path, content, score, terms, source)]
        ranked.sort(key=lambda r: (-r[0], r[1].start_line))
        best = ranked[0][0]
        chunks = []
        for s, sym, _ in ranked[:_MAX_SYMBOL_CHUNKS_PER_FILE]:
            body = "\n".join(lines[sym.start_line - 1:sym.end_line])
            if len(body) > _MAX_FILE_CHARS:
                body = body[:_MAX_FILE_CHARS] + "\n...(truncated)..."
            chunks.append(RetrievedContextChunk(
                file_path=path, start_line=sym.start_line, end_line=sym.end_line, content=body,
                score=round(score * s / best, 3), symbol=sym.qualname, source=source))
        return chunks

    @staticmethod
    def _locate(workspace_path: str | None, path: str, content: str, score: float) -> RetrievedContextChunk | None:
        """Give an indexed chunk its real line range; drop it if the checkout no longer contains it."""
        if not workspace_path:
            return ContextBuilder._chunk(path, content, score, source="semantic")
        text = read_repo_file(workspace_path, path)
        pos = text.find(content) if text else -1
        if pos < 0:
            return None
        start = text.count("\n", 0, pos) + 1
        end = start + content.count("\n")
        return RetrievedContextChunk(file_path=path, start_line=start, end_line=end,
                                     content=content[:_MAX_FILE_CHARS], score=score, source="semantic")

    def _lexical_search(self, workspace_path: str, tracked: set, terms: List[str],
                        symbol_index=None) -> List[RetrievedContextChunk]:
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
        chunks = []
        for score, path, content in top:
            chunks.extend(self._file_chunks(path, content, round(score / best, 3), terms, symbol_index, "lexical"))
        return chunks
