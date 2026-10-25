"""In-memory index of the Python classes, functions and methods in one checkout.

Built per run from the exact revision being analyzed, so it can never be stale.
Qualified names come from the same walker the patch service uses for symbol
edits ('func', 'Class', 'Class.method', 'Outer.Inner'), so a symbol named in a
plan is one the coding step can address. Functions nested inside functions are
local and therefore not indexed. Non-Python files are known by path only.
"""
import ast
import logging
from dataclasses import dataclass
from typing import Iterable, List, Optional
from app.config import settings
from app.agents.prompting import read_repo_file
from app.services.patch_service import _python_symbols
from app.services.repository_scanner import _is_ignored

logger = logging.getLogger(__name__)

MAX_INHERITANCE_DEPTH = 5
_LARGE_CLASS_LINES = 150


@dataclass(frozen=True)
class Symbol:
    qualname: str
    kind: str  # "class", "function" or "method"
    file_path: str
    start_line: int  # first decorator line, if any
    end_line: int
    header_end: int  # last line of the signature (before the body)
    bases: tuple = ()
    params: tuple = ()      # argument names, without self/cls
    decorators: tuple = ()  # decorator source, e.g. 'router.get("", response_model=X)'

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]

    @property
    def parent(self) -> str:
        return self.qualname.rsplit(".", 1)[0] if "." in self.qualname else ""

    @property
    def span(self) -> int:
        return self.end_line - self.start_line + 1

    def label(self) -> str:
        bases = f"({', '.join(self.bases)})" if self.bases else ""
        return f"{self.file_path}:{self.start_line}-{self.end_line} {self.kind} {self.qualname}{bases}"


def _base_names(node: ast.ClassDef) -> tuple:
    names = []
    for b in node.bases:
        if isinstance(b, ast.Name):
            names.append(b.id)
        elif isinstance(b, ast.Attribute):
            names.append(b.attr)
    return tuple(names)


def _param_names(node) -> tuple:
    a = node.args
    names = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    names += [x.arg for x in (a.vararg, a.kwarg) if x]
    return tuple(n for n in names if n not in ("self", "cls"))


def _decorator_source(node) -> tuple:
    out = []
    for d in node.decorator_list:
        try:
            out.append(ast.unparse(d))
        except Exception:
            pass
    return tuple(out)


def _setting_int(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


class SymbolIndex:
    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.files: set = set()          # every tracked file, any language
        self.python_files: set = set()   # Python files that were read (parsed or not)
        self.parse_errors: dict = {}     # path -> reason
        self.symbols: List[Symbol] = []
        self._lines: dict = {}
        self._by_file: dict = {}
        self._by_qualname: dict = {}
        self._by_name: dict = {}

    # ---- construction ----

    @classmethod
    def build(cls, workspace_path: str, tracked_files: Iterable[str]) -> "SymbolIndex":
        index = cls(workspace_path)
        index.files = set(tracked_files)
        max_bytes = _setting_int("MAX_FILE_SIZE_MB", 1) * 1024 * 1024
        for path in sorted(index.files):
            if not path.endswith(".py") or _is_ignored(path):
                continue
            text = read_repo_file(workspace_path, path)
            if text is None:
                continue
            if len(text) > max_bytes:
                index.parse_errors[path] = "file too large to index"
                continue
            index.python_files.add(path)
            index._lines[path] = text.split("\n")
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError) as e:
                index.parse_errors[path] = f"does not parse: {getattr(e, 'msg', e)} (line {getattr(e, 'lineno', '?')})"
                continue
            index._add_tree(path, tree)
        logger.info("Symbol index: %d symbols in %d Python files (%d not parsed)",
                    len(index.symbols), len(index.python_files), len(index.parse_errors))
        return index

    def _add_tree(self, path: str, tree: ast.AST):
        for qualname, nodes in _python_symbols(tree).items():
            for node in nodes:
                is_class = isinstance(node, ast.ClassDef)
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                body_first = node.body[0].lineno if node.body else node.lineno
                sym = Symbol(
                    qualname=qualname,
                    kind="class" if is_class else ("method" if "." in qualname else "function"),
                    file_path=path,
                    start_line=start,
                    end_line=node.end_lineno or node.lineno,
                    header_end=max(node.lineno, body_first - 1),
                    bases=_base_names(node) if is_class else (),
                    params=() if is_class else _param_names(node),
                    decorators=_decorator_source(node),
                )
                self.symbols.append(sym)
                self._by_file.setdefault(path, []).append(sym)
                self._by_qualname.setdefault(qualname, []).append(sym)
                self._by_name.setdefault(sym.name, []).append(sym)
        self._by_file.get(path, []).sort(key=lambda s: (s.start_line, -s.end_line))

    # ---- file access ----

    def has_file(self, path: str) -> bool:
        return path in self.files

    def lines(self, path: str) -> Optional[List[str]]:
        if path not in self.files:
            return None
        if path not in self._lines:
            text = read_repo_file(self.workspace_path, path)
            if text is None:
                return None
            self._lines[path] = text.split("\n")
        return self._lines[path]

    def line_count(self, path: str) -> Optional[int]:
        lines = self.lines(path)
        return None if lines is None else len(lines)

    def read_lines(self, path: str, start: int, end: int, max_lines: int = 80, numbered: bool = True) -> str:
        lines = self.lines(path) or []
        start, end = max(start, 1), min(end, len(lines))
        shown_end = min(end, start + max_lines - 1)
        out = [f"{n:>5} | {lines[n - 1]}" if numbered else lines[n - 1] for n in range(start, shown_end + 1)]
        if shown_end < end:
            out.append(f"  ... ({end - shown_end} more lines)")
        return "\n".join(out)

    # ---- lookups ----

    def symbols_in_file(self, path: str) -> List[Symbol]:
        return list(self._by_file.get(path, []))

    def find_class(self, name: str) -> List[Symbol]:
        name = name.strip()
        found = self._by_qualname.get(name, []) if "." in name else self._by_name.get(name, [])
        return [s for s in found if s.kind == "class"]

    def find_function(self, name: str) -> List[Symbol]:
        name = name.strip()
        found = self._by_qualname.get(name, []) if "." in name else self._by_name.get(name, [])
        return [s for s in found if s.kind != "class"]

    def find_method_in_class(self, method: str, class_name: str) -> List[Symbol]:
        """The class's own definition(s) of `method`; otherwise the nearest inherited ones."""
        frontier = self.find_class(class_name)
        seen = set()
        for _ in range(MAX_INHERITANCE_DEPTH + 1):
            found, parents = [], []
            for cls in frontier:
                if (cls.file_path, cls.qualname) in seen:
                    continue
                seen.add((cls.file_path, cls.qualname))
                found += [s for s in self._by_qualname.get(f"{cls.qualname}.{method}", [])
                          if s.file_path == cls.file_path]
                for base in cls.bases:
                    parents += self.find_class(base)
            if found or not parents:
                return found
            frontier = parents
        return []

    def enclosing_symbol(self, path: str, line: int) -> Optional[Symbol]:
        """The innermost indexed symbol containing `line`."""
        containing = [s for s in self._by_file.get(path, []) if s.start_line <= line <= s.end_line]
        return min(containing, key=lambda s: s.span) if containing else None

    def resolve(self, symbol: str, path: str | None = None) -> List[Symbol]:
        """Symbols matching a (possibly module-prefixed) qualified or plain name, optionally in one file."""
        name = symbol.strip().strip("`'\"")
        if name.endswith("()"):
            name = name[:-2]
        if not name:
            return []
        pool = self._by_file.get(path, []) if path else self.symbols
        parts = name.split(".")
        # 'pkg.module.Class.method' -> try 'Class.method', 'method' only after qualified forms fail.
        for i in range(len(parts)):
            candidate = ".".join(parts[i:])
            matches = [s for s in pool if s.qualname == candidate]
            if matches:
                return matches
        if len(parts) == 1:
            return [s for s in pool if s.name == name]
        return []

    def find_text(self, text: str, limit: int = 20) -> List[tuple]:
        """Literal occurrences of `text` in indexed Python files: (path, line, enclosing symbol)."""
        if not text.strip():
            return []
        hits = []
        for path in sorted(self.python_files):
            for n, line in enumerate(self._lines.get(path, []), start=1):
                if text in line:
                    hits.append((path, n, self.enclosing_symbol(path, n)))
                    if len(hits) >= limit:
                        return hits
        return hits

    # ---- rendering ----

    def outline(self, sym: Symbol) -> str:
        """Signature-level view: the symbol's header plus its direct members' headers."""
        parts = [self.read_lines(sym.file_path, sym.start_line, sym.header_end)]
        for child in self._by_file.get(sym.file_path, []):
            if child.parent == sym.qualname:
                parts.append(self.read_lines(child.file_path, child.start_line, child.header_end))
        return "\n".join(parts)

    def snippet(self, sym: Symbol, max_lines: int = 60) -> str:
        """Full numbered code, or the outline for a class too long to show whole."""
        if sym.kind == "class" and sym.span > max_lines:
            return self.outline(sym)
        return self.read_lines(sym.file_path, sym.start_line, sym.end_line, max_lines=max_lines)

    def is_large_class(self, sym: Symbol) -> bool:
        return sym.kind == "class" and sym.span > _LARGE_CLASS_LINES
