import os
import re
import ast
import difflib
import hashlib
import logging
import textwrap
from app.config import settings
from app.agents.schemas import CodeChangePlan, CodeChange

logger = logging.getLogger(__name__)


class PatchError(ValueError):
    """A proposed change cannot be applied; the message is suitable to feed back to the model."""
    category = "INVALID_PATCH"


class ScopeViolationError(PatchError):
    """The change touches a file the plan did not authorize for this step."""
    category = "SCOPE_VIOLATION"


class PatchSyntaxError(PatchError):
    """The edited file would not parse."""
    category = "SYNTAX_ERROR"


class ContextMismatchError(PatchError):
    """An edit's search text / symbol does not identify exactly one place in the current file."""
    category = "CONTEXT_MISMATCH"


def _python_symbols(tree: ast.AST) -> dict:
    """Map qualified names ('func', 'Class', 'Class.method') to their AST nodes."""
    found = {}
    def visit(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{node.name}"
                found.setdefault(name, []).append(node)
                if isinstance(node, ast.ClassDef):
                    visit(node.body, name + ".")
    visit(tree.body, "")
    return found


def _replace_symbol(text: str, symbol: str, replacement: str, path: str, i: int) -> str:
    """Replace a whole Python function/method/class definition, located by qualified name."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise PatchSyntaxError(f"{path}: cannot locate symbol for edit {i}: file does not parse ({e.msg} at line {e.lineno}).")
    symbols = _python_symbols(tree)
    name = symbol.strip()
    matches = symbols.get(name) or [n for q, nodes in symbols.items() if q.split(".")[-1] == name for n in nodes]
    if len(matches) != 1:
        available = ", ".join(sorted(symbols)) or "(none)"
        problem = "is ambiguous" if matches else "was not found"
        raise ContextMismatchError(
            f"{path}: edit {i} symbol '{symbol}' {problem}. Use one of these qualified names: {available}.")
    node = matches[0]
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) - 1
    lines = text.split("\n")
    indent = " " * node.col_offset
    new_block = [indent + l if l.strip() else "" for l in textwrap.dedent(replacement.strip("\n")).split("\n")]
    return "\n".join(lines[:start] + new_block + lines[node.end_lineno:])


def _normalize_ws(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))


def _whitespace_insensitive_span(text: str, search: str) -> tuple[int, int] | None:
    """Locate `search` in `text` ignoring all whitespace (e.g. a multi-line signature the
    model reflowed onto one line). Returns the (start, end) span in `text` only when the
    match is unique and substantial; otherwise None."""
    compact_search = "".join(search.split())
    if len(compact_search) < 20:
        return None
    positions = [idx for idx, ch in enumerate(text) if not ch.isspace()]
    compact_text = "".join(text[idx] for idx in positions)
    first = compact_text.find(compact_search)
    if first == -1 or compact_text.find(compact_search, first + 1) != -1:
        return None
    return positions[first], positions[first + len(compact_search) - 1] + 1


def _closest_snippet(text: str, search: str, max_lines: int = 40) -> str | None:
    """The block of `text` most similar to `search` (same number of lines), for feedback to the model."""
    lines = text.split("\n")
    target = search.strip("\n")
    n = min(max(len(target.split("\n")), 1), max_lines)
    # A model that reflowed text onto fewer lines usually omitted lines; widen the window a bit.
    candidates = {n, min(n + 4, len(lines)), min(n * 2, len(lines), max_lines)}
    best, best_ratio = None, 0.0
    for size in candidates:
        for start in range(0, max(len(lines) - size + 1, 1)):
            block = "\n".join(lines[start:start + size])
            ratio = difflib.SequenceMatcher(None, block, target, autojunk=False).quick_ratio()
            if ratio <= best_ratio:
                continue
            ratio = difflib.SequenceMatcher(None, block, target, autojunk=False).ratio()
            if ratio > best_ratio:
                best, best_ratio = (start, size), ratio
    if best is None or best_ratio < 0.5:
        return None
    # Quote a little real context around the match so the next attempt can copy a
    # complete, unique block instead of another fragment.
    start, size = best
    return "\n".join(lines[max(start - 2, 0):start + size + 2])


def apply_edits(original: str, change: CodeChange) -> str:
    """Apply search/replace edits to a file's text. Every search must match exactly once."""
    uses_crlf = "\r\n" in original
    text = original.replace("\r\n", "\n")
    for i, edit in enumerate(change.edits, 1):
        search = edit.search.replace("\r\n", "\n")
        replace = edit.replace.replace("\r\n", "\n")
        if edit.symbol.strip():
            if not change.file_path.endswith(".py"):
                raise ContextMismatchError(f"{change.file_path}: edit {i} uses 'symbol', which only works for Python files; use 'search'.")
            text = _replace_symbol(text, edit.symbol, replace, change.file_path, i)
            continue
        if not search.strip():
            raise ContextMismatchError(f"{change.file_path}: edit {i} needs either 'symbol' or a non-empty 'search' block.")
        count = text.count(search)
        if count == 1:
            text = text.replace(search, replace, 1)
            continue
        if count > 1:
            raise ContextMismatchError(
                f"{change.file_path}: edit {i} 'search' text occurs {count} times; include more surrounding lines so it is unique."
            )
        # Tolerate trailing-whitespace differences, which models often get wrong.
        lines = text.split("\n")
        norm_lines = [l.rstrip() for l in lines]
        search_lines = _normalize_ws(search).split("\n")
        while search_lines and search_lines[-1] == "":
            search_lines.pop()
        n = len(search_lines)
        matches = [s for s in range(len(lines) - n + 1) if norm_lines[s:s + n] == search_lines]
        if len(matches) != 1:
            span = _whitespace_insensitive_span(text, search)
            if span is None:
                logger.info(f"Rejected search block for {change.file_path} edit {i}:\n{search[:1500]}")
                preview = search.strip().split("\n")[0][:120]
                original_norm = original.replace("\r\n", "\n")
                if i > 1 and (search in original_norm or _whitespace_insensitive_span(original_norm, search)):
                    raise ContextMismatchError(
                        f"{change.file_path}: edit {i} 'search' text was already changed by an earlier edit in this "
                        "file. Combine overlapping edits into a single search/replace block."
                    )
                closest = _closest_snippet(text, search)
                hint = (f" The closest text in the file is:\n<<<\n{closest}\n>>>\nCopy it character for character "
                        "(including trailing commas and line breaks) into 'search'.") if closest else \
                       " Copy the text exactly from the file content provided."
                if change.file_path.endswith(".py"):
                    hint += " To rewrite a whole function or method, use 'symbol' (e.g. 'ClassName.method') instead of 'search'."
                raise ContextMismatchError(
                    f"{change.file_path}: edit {i} 'search' text was not found in the current file "
                    f"(first line: {preview!r}).{hint}"
                )
            text = text[:span[0]] + replace.strip("\n") + text[span[1]:]
            continue
        start = matches[0]
        replace_lines = replace.split("\n")
        if replace.endswith("\n"):
            replace_lines = replace_lines[:-1]
        text = "\n".join(lines[:start] + replace_lines + lines[start + n:])
    return text.replace("\n", "\r\n") if uses_crlf else text


class PatchService:
    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.realpath(workspace_path)
        self.max_files = getattr(settings, "MAX_CHANGED_FILES", 20)
        self.max_diff_bytes = getattr(settings, "MAX_DIFF_BYTES", 500000)
        self.allow_delete = getattr(settings, "ALLOW_FILE_DELETE", False)

    def _hash_file(self, exact_path: str) -> str:
        if not os.path.exists(exact_path): return ""
        with open(exact_path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()

    def _validate_path(self, rel_path: str) -> str:
        # absolute rejections mapping both Unix and Windows structural traversals natively.
        if rel_path.startswith("/") or rel_path.startswith("\\") or re.match(r"^[A-Za-z]:", rel_path) or ":" in rel_path:
            raise PatchError(f"Absolute paths rejected: {rel_path}")
        parts = re.split(r"[\\/]+", rel_path)
        if ".." in parts:
            raise PatchError(f"Traversal rejected: {rel_path}")
        if ".git" in parts:
            raise PatchError(f".git modification rejected: {rel_path}")

        exact = os.path.realpath(os.path.join(self.workspace_path, rel_path))
        if not exact.startswith(self.workspace_path + os.sep):
            raise PatchError(f"Symlink or escape path rejected: {rel_path}")
        return exact

    @staticmethod
    def _read_text(path: str, rel: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", newline="") as f:
                return f.read()
        except UnicodeDecodeError:
            raise PatchError(f"{rel}: refusing to modify a binary or non-UTF-8 file.")

    def _compute(self, change: CodeChange, target: str) -> tuple[str | None, str | None]:
        """Return (old_text, new_text) for one change without touching the disk. None = absent."""
        rel = change.file_path
        exists = os.path.isfile(target)
        if change.operation == "delete":
            if not self.allow_delete:
                raise PatchError(f"Delete operation requested but disabled globally: {rel}")
            if not exists:
                raise PatchError(f"{rel}: cannot delete a file that does not exist.")
            return self._read_text(target, rel), None

        if change.operation == "create":
            if exists:
                raise PatchError(f"{rel}: file already exists; use operation 'modify' with search/replace edits.")
            if not change.content:
                raise PatchError(f"{rel}: 'create' requires the full file content.")
            return None, change.content

        # modify
        if not exists:
            raise PatchError(f"{rel}: file does not exist; use operation 'create' to add a new file.")
        old = self._read_text(target, rel)
        if change.edits:
            new = apply_edits(old, change)
        elif change.content:
            # Whole-file replacement. Small models often return truncated files,
            # so refuse replacements that silently drop most of the original.
            old_lines = len(old.splitlines())
            new_lines = len(change.content.splitlines())
            if old_lines >= 20 and new_lines < old_lines * 0.5:
                raise PatchError(
                    f"{rel}: full-file replacement would shrink the file from {old_lines} to {new_lines} lines; "
                    "use targeted search/replace edits instead."
                )
            new = change.content
        else:
            raise PatchError(f"{rel}: 'modify' requires at least one search/replace edit.")
        return old, new

    def check_plan(self, plan: CodeChangePlan) -> None:
        """Raise PatchError if the plan cannot be applied cleanly; never writes."""
        self._prepare(plan)

    def apply_plan(self, plan: CodeChangePlan) -> dict:
        # Validate and compute everything first so a bad change leaves the workspace untouched.
        planned, total_diff_str = self._prepare(plan)

        metrics = []
        for rel, target, old, new in planned:
            orig_hash = self._hash_file(target)
            orig_sz = os.path.getsize(target) if os.path.exists(target) else 0
            if new is None:
                os.remove(target)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8", newline="") as fw:
                    fw.write(new)
            metrics.append({
                "path": rel, "orig_hash": orig_hash, "new_hash": self._hash_file(target),
                "orig_size": orig_sz, "new_size": os.path.getsize(target) if os.path.exists(target) else 0,
            })
            logger.info(f"Applied change to {rel}")

        return {"summary": total_diff_str, "metrics": metrics, "changed_files": [m["path"] for m in metrics]}

    @staticmethod
    def _check_python_syntax(rel: str, old: str | None, new: str) -> None:
        """Reject edits that turn a parseable Python file into an unparseable one.

        Catching this here costs nothing, whereas letting it through costs a
        sandbox run and a whole repair cycle on a "collection error"."""
        if old is not None:
            try:
                ast.parse(old)
            except SyntaxError:
                return  # the file was already unparseable (e.g. a template); don't block on it
        try:
            ast.parse(new)
        except SyntaxError as e:
            lines = new.splitlines()
            ctx = "\n".join(f"{n}: {lines[n - 1]}" for n in range(max(1, (e.lineno or 1) - 2), min(len(lines), (e.lineno or 1) + 2) + 1))
            raise PatchSyntaxError(
                f"{rel}: the edited file is not valid Python ({e.msg} at line {e.lineno}); the original file was left "
                f"unchanged. Lines around the error in your version:\n{ctx}")

    def _prepare(self, plan: CodeChangePlan):
        if not plan.changes:
            raise PatchError("The change set is empty.")
        if len(plan.changes) > self.max_files:
            raise PatchError(f"Exceeded max files threshold: {len(plan.changes)}")

        planned = []
        seen = set()
        for change in plan.changes:
            target = self._validate_path(change.file_path)
            rel = os.path.relpath(target, self.workspace_path).replace("\\", "/")
            if rel in seen:
                raise PatchError(f"{rel}: listed more than once; combine its edits into one change.")
            seen.add(rel)
            old, new = self._compute(change, target)
            if new is not None and "\x00" in new:
                raise PatchError(f"{rel}: binary content is not allowed.")
            if old == new:
                raise PatchError(f"{rel}: the change does not alter the file.")
            if new is not None and rel.endswith(".py"):
                self._check_python_syntax(rel, old, new)
            planned.append((rel, target, old, new))

        diffs = []
        for rel, _, old, new in planned:
            diffs.append("".join(difflib.unified_diff(
                (old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
                fromfile="/dev/null" if old is None else f"a/{rel}",
                tofile="/dev/null" if new is None else f"b/{rel}",
            )))
        total_diff_str = "\n".join(diffs)
        if len(total_diff_str.encode("utf-8")) > self.max_diff_bytes:
            raise PatchError("Diff capacity boundary limit gracefully exceeded.")
        return planned, total_diff_str
