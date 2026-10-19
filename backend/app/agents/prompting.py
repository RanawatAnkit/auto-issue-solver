"""Helpers for building size-bounded prompts from repository content."""
import os
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
