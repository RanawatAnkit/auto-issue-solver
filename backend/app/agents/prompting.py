"""Helpers for building size-bounded prompts from repository content."""
import ast
import os
import re
from typing import Iterable, List


def tail(text: str, limit: int) -> str:
    """Keep the end of long output: pytest prints tracebacks and the summary last."""
    text = text or ""
    if len(text) <= limit:
        return text
    return "...(earlier output truncated)...\n" + text[-limit:]


def head(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...(truncated)..."


def read_repo_file(workspace_path: str, rel_path: str) -> str | None:
    """Read a text file inside the workspace; None if missing, outside the checkout, or binary."""
    root = os.path.realpath(workspace_path)
    full = os.path.realpath(os.path.join(root, rel_path))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None
    try:
        with open(full, "r", encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return None


def files_block(workspace_path: str, paths: Iterable[str], budget_chars: int) -> tuple[str, List[str]]:
    """Render the full content of each file, in order, until the budget runs out.

    Files are included whole or not at all, because search/replace edits must
    quote the file exactly. Returns (text, paths_included).
    """
    parts, included, used = [], [], 0
    seen = set()
    for rel in paths:
        if rel in seen:
            continue
        seen.add(rel)
        content = read_repo_file(workspace_path, rel)
        if content is None:
            continue
        block = f"===== FILE: {rel} =====\n{content}\n===== END FILE: {rel} =====\n"
        if used + len(block) > budget_chars:
            continue
        parts.append(block)
        included.append(rel)
        used += len(block)
    return "\n".join(parts), included


_HEAD_LINES = 40     # imports and module-level setup
_MATCH_WINDOW = (10, 30)  # lines before/after a textual mention of a symbol
_MAX_MATCHES = 6


def _python_definition_spans(content: str, symbols: Iterable[str]) -> List[tuple[int, int]]:
    """0-based [start, end) line spans of the named functions/classes/methods."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    wanted = {s.strip() for s in symbols if s and s.strip()}
    spans = []

    def visit(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}{node.name}"
                if qualified in wanted or node.name in wanted:
                    start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
                    spans.append((start, node.end_lineno))
                if isinstance(node, ast.ClassDef):
                    visit(node.body, qualified + ".")

    visit(tree.body, "")
    return spans


def excerpt_block(workspace_path: str, rel_path: str, symbols: Iterable[str], budget_chars: int) -> str | None:
    """A partial view of a file too large to show whole: its head, the definitions of
    `symbols`, and the lines around other mentions of them, in file order.

    Omitted regions are marked on their own line. Edits made from an excerpt are still
    validated against the complete file by PatchService, so a partial view cannot
    produce an edit in the wrong place. Returns None for unreadable files.
    """
    content = read_repo_file(workspace_path, rel_path)
    if content is None:
        return None
    lines = content.split("\n")
    symbols = [s for s in symbols if s and s.strip()]
    # Spans in priority order: definitions, head, then textual mentions.
    spans = _python_definition_spans(content, symbols) if rel_path.endswith(".py") else []
    spans.append((0, min(_HEAD_LINES, len(lines))))
    names = {s.strip().split(".")[-1] for s in symbols}
    matches = 0
    for i, line in enumerate(lines):
        if matches >= _MAX_MATCHES:
            break
        if any(re.search(rf"\b{re.escape(n)}\b", line) for n in names):
            spans.append((max(i - _MATCH_WINDOW[0], 0), min(i + _MATCH_WINDOW[1], len(lines))))
            matches += 1

    chosen: List[tuple[int, int]] = []
    used = 0
    for start, end in spans:
        size = sum(len(l) + 1 for l in lines[start:end])
        if used + size > budget_chars:
            continue
        chosen.append((start, end))
        used += size
    if not chosen:
        return None

    merged: List[list[int]] = []
    for start, end in sorted(chosen):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts, cursor = [], 0
    for start, end in merged:
        if start > cursor:
            parts.append(f"... [lines {cursor + 1}-{start} not shown] ...")
        parts.append("\n".join(lines[start:end]))
        cursor = end
    if cursor < len(lines):
        parts.append(f"... [lines {cursor + 1}-{len(lines)} not shown] ...")
    body = "\n".join(parts)
    return (f"===== FILE (EXCERPT, {len(lines)} lines in total): {rel_path} =====\n{body}\n"
            f"===== END FILE: {rel_path} =====\n")


def related_test_support_files(tracked_files: Iterable[str], paths: Iterable[str]) -> List[str]:
    """conftest.py / test helper files that sit beside or above the given test files.

    Generated tests must use fixtures that actually exist; showing the model the
    relevant conftest.py files is what prevents "fixture 'x' not found" loops.
    """
    tracked = set(tracked_files)
    support = []
    for p in paths:
        parts = p.split("/")[:-1]
        for i in range(len(parts), -1, -1):
            candidate = "/".join(parts[:i] + ["conftest.py"]) if i else "conftest.py"
            if candidate in tracked and candidate not in support:
                support.append(candidate)
    return support
