"""How well retrieval finds the code a fix needs, measured against SWE-bench gold patches. No LLM.

For each task, ContextBuilder runs on the base commit the way the agent's pipeline runs it, and its
chunks (in the order the planner sees them) are compared with what the maintainers' fix changed:
the source files, and the functions/methods/classes enclosing each changed line.

The issue analysis normally comes from an LLM; here it is built from the problem statement (title
line as the summary, the rest as current behaviour), so the numbers are free, repeatable and
measure retrieval alone.
"""
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.agents.context_builder import ContextBuilder
from app.agents.planner import is_test_file
from app.agents.schemas import IssueAnalysis
from app.eval import grading
from app.eval.repos import RepoCache, remove_workspace
from app.services.repository_scanner import list_tracked_files
from app.services.symbol_index import SymbolIndex

K_VALUES = (1, 3, 5, 10)
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def gold_changes(patch: str) -> dict[str, set[int]]:
    """Source file -> lines of the ORIGINAL file the patch changes. A pure insertion counts as the
    line it is inserted after (or line 1), so it maps to the definition it lands in."""
    changed: dict[str, set[int]] = {}
    current, old_line, in_header = None, 0, False
    for line in (patch or "").splitlines():
        if line.startswith("diff --git "):
            current, in_header = None, True
        elif in_header and line.startswith("--- "):
            continue
        elif in_header and line.startswith("+++ "):
            path = line[4:].strip()
            path = path[2:] if path.startswith("b/") else path
            current = None if path == "/dev/null" or is_test_file(path) else path
            if current is not None:
                changed.setdefault(current, set())
        elif m := _HUNK_RE.match(line):
            in_header = False
            old_line = int(m.group(1))
        elif in_header:
            continue
        elif current is not None and line.startswith("-"):
            changed[current].add(old_line)
            old_line += 1
        elif current is not None and line.startswith("+"):
            changed[current].add(max(old_line - 1, 1))
        elif current is not None and line.startswith(" "):
            old_line += 1
    return changed


def gold_symbols(changes: dict[str, set[int]], index: SymbolIndex) -> list[tuple[str, str, int, int]]:
    """(file, qualified name, start, end) of the innermost symbol around each changed line."""
    found = {}
    for path, lines in changes.items():
        for n in lines:
            sym = index.enclosing_symbol(path, n)
            if sym is not None:
                found[(path, sym.qualname)] = (path, sym.qualname, sym.start_line, sym.end_line)
    return sorted(found.values())


def analysis_from_statement(statement: str) -> IssueAnalysis:
    title, _, rest = statement.strip().partition("\n")
    return IssueAnalysis(problem_summary=title.strip(), current_behavior=rest.strip()[:2000], expected_behavior="")


@dataclass
class TaskRetrieval:
    instance_id: str
    gold_files: list[str]
    gold_symbols: list[str]
    retrieved_files: list[str]            # unique, in the order first retrieved
    file_rank: int | None                 # 1-based rank of the first gold file; None = not retrieved
    files_found: int                      # gold files retrieved at any rank
    symbol_ranks: dict = field(default_factory=dict)  # gold symbol -> 1-based chunk rank, or None
    chunks: int = 0
    seed_hit: bool = False                # a deterministic hint pointed straight at a gold symbol
    error: str = ""


def evaluate_task(task: dict, repos: RepoCache) -> TaskRetrieval:
    changes = gold_changes(task["patch"])
    workspace = repos.checkout(task["repo"], task["base_commit"])
    try:
        tracked = list_tracked_files(workspace)
        index = SymbolIndex.build(workspace, tracked)
        builder = ContextBuilder(None)  # no database: the agent's eval runs retrieve lexically too
        chunks = builder.build_context(0, analysis_from_statement(task["problem_statement"]), workspace, tracked,
                                       symbol_index=index, issue_text=task["problem_statement"])
        symbols = gold_symbols(changes, index)
    finally:
        remove_workspace(workspace)

    retrieved = list(dict.fromkeys(c.file_path for c in chunks))
    file_ranks = [retrieved.index(f) + 1 for f in changes if f in retrieved]
    symbol_ranks = {}
    for path, qualname, start, end in symbols:
        rank = next((i for i, c in enumerate(chunks, 1)
                     if c.file_path == path and c.start_line <= end and start <= c.end_line), None)
        symbol_ranks[f"{path}::{qualname}"] = rank
    seeds = {(s.file_path, s.symbol) for s in builder.seed_locations}
    return TaskRetrieval(
        instance_id=task["instance_id"], gold_files=sorted(changes), gold_symbols=list(symbol_ranks),
        retrieved_files=retrieved, file_rank=min(file_ranks) if file_ranks else None,
        files_found=len(file_ranks), symbol_ranks=symbol_ranks, chunks=len(chunks),
        seed_hit=any((p, q) in seeds for p, q, _, _ in symbols))


def summarize(results: list[TaskRetrieval]) -> dict:
    ok = [r for r in results if not r.error]
    n = len(ok) or 1
    sym_ranks = [rank for r in ok for rank in r.symbol_ranks.values()]
    out = {"tasks": len(ok), "errors": len(results) - len(ok)}
    for k in K_VALUES:
        out[f"file_recall@{k}"] = round(100 * sum(1 for r in ok if r.file_rank and r.file_rank <= k) / n, 1)
    out["file_recall@all"] = round(100 * sum(1 for r in ok if r.file_rank) / n, 1)
    if sym_ranks:
        for k in (5, 10):
            out[f"symbol_recall@{k}"] = round(100 * sum(1 for x in sym_ranks if x and x <= k) / len(sym_ranks), 1)
        out["symbol_recall@all"] = round(100 * sum(1 for x in sym_ranks if x) / len(sym_ranks), 1)
    out["gold_symbols"] = len(sym_ranks)
    out["seed_hits"] = sum(1 for r in ok if r.seed_hit)
    return out


def run(tasks: list[dict], cache_dir: str, results_dir: str, on_result=None) -> tuple[list[TaskRetrieval], dict, str]:
    repos = RepoCache(cache_dir)
    results = []
    for task in tasks:
        try:
            r = evaluate_task(task, repos)
        except Exception as e:
            r = TaskRetrieval(task["instance_id"], [], [], [], None, 0, error=f"{type(e).__name__}: {e}"[:300])
        results.append(r)
        if on_result:
            on_result(r)
    summary = summarize(results)
    os.makedirs(results_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(results_dir, f"retrieval-{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "tasks": [asdict(r) for r in results]}, f, indent=1)
    return results, summary, path
