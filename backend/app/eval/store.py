"""Evaluation results: an append-only JSONL history plus per-result artifacts.

Layout under EVAL_RESULTS_DIR:
    history.jsonl                         one TaskResult per line, newest last
    runs/<run_id>/<instance_id>.<mode>.log  full test log and patch for that result
"""
import json
import os
import threading
from collections import Counter

from app.eval.harness import AGENT, ATTEMPTED_OUTCOMES, GOLD, RESOLVED, TaskResult

_lock = threading.Lock()


class ResultsStore:
    def __init__(self, results_dir: str):
        self.dir = results_dir
        self.history_path = os.path.join(results_dir, "history.jsonl")

    def append(self, result: TaskResult) -> None:
        record = result.to_dict()
        with _lock:
            os.makedirs(self.dir, exist_ok=True)
            artifact_dir = os.path.join(self.dir, "runs", result.run_id)
            os.makedirs(artifact_dir, exist_ok=True)
            base = os.path.join(artifact_dir, f"{result.instance_id}.{result.mode}")
            with open(base + ".patch", "w", encoding="utf-8", newline="\n") as f:
                f.write(result.patch or "")
            if result.full_test_log:
                with open(base + ".log", "w", encoding="utf-8", newline="\n") as f:
                    f.write(result.full_test_log)
            record["artifacts"] = os.path.relpath(base, self.dir).replace("\\", "/")
            with open(self.history_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(record) + "\n")

    def history(self) -> list[dict]:
        if not os.path.exists(self.history_path):
            return []
        records = []
        with open(self.history_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a line cut short by a crash; the rest of the file is still valid
        return records

    def latest(self, mode: str) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for record in self.history():
            if record.get("mode") == mode:
                latest[record["instance_id"]] = record  # history is chronological
        return latest


def pending_tasks(tasks: list[dict], store: ResultsStore) -> list[dict]:
    """Tasks the agent has not had a fair attempt at yet: never run, or only
    infrastructure failures (e.g. a spent LLM quota). Used to resume a run."""
    latest = store.latest(AGENT)
    return [t for t in tasks
            if t["instance_id"] not in latest or latest[t["instance_id"]]["outcome"] not in ATTEMPTED_OUTCOMES]


def by_model(tasks: list[dict], store: ResultsStore) -> list[dict]:
    """Per model: its latest result on each task it ran. The headline numbers use the latest result
    of any model, so a later run with a weaker model would otherwise hide an earlier success."""
    ids = {t["instance_id"] for t in tasks}
    latest: dict[str, dict[str, dict]] = {}
    for record in store.history():
        if record.get("mode") == AGENT and record["instance_id"] in ids:
            latest.setdefault(record.get("model") or "unknown", {})[record["instance_id"]] = record
    rows = []
    for model, results in latest.items():
        outcomes = Counter(r["outcome"] for r in results.values())
        attempted = sum(outcomes[o] for o in ATTEMPTED_OUTCOMES)
        rows.append({
            "model": model, "provider": next(iter(results.values())).get("provider"),
            "evaluated": len(results), "attempted": attempted, "resolved": outcomes[RESOLVED],
            "resolved_ids": sorted(k for k, r in results.items() if r["outcome"] == RESOLVED),
            "outcomes": dict(outcomes),
        })
    return sorted(rows, key=lambda r: (-r["resolved"], r["model"]))


def summarize(tasks: list[dict], store: ResultsStore) -> dict:
    """Headline numbers over the latest agent result of each benchmark task."""
    ids = {t["instance_id"] for t in tasks}
    latest = {k: v for k, v in store.latest(AGENT).items() if k in ids}
    gold = {k: v for k, v in store.latest(GOLD).items() if k in ids}
    outcomes = Counter(r["outcome"] for r in latest.values())
    # If the reference fix fails in our environment, so would any correct agent
    # fix: such tasks cannot judge the agent and are left out of the attempted rate.
    invalid = {k for k, r in gold.items() if r["outcome"] != RESOLVED}
    fair = [r for k, r in latest.items() if k not in invalid and r["outcome"] in ATTEMPTED_OUTCOMES]
    attempted = len(fair)
    resolved = outcomes[RESOLVED]
    resolved_fair = sum(1 for r in fair if r["outcome"] == RESOLVED)
    runtimes = [r["runtime_seconds"] for r in latest.values() if r.get("runtime_seconds")]
    last = max(latest.values(), key=lambda r: r.get("completed_at", ""), default=None)
    return {
        "total_tasks": len(tasks),
        "evaluated": len(latest),
        "attempted": attempted,
        "resolved": resolved,
        # Resolved among tasks where the agent had a fair attempt (infrastructure failures excluded)...
        "resolution_rate_attempted": round(100 * resolved_fair / attempted, 1) if attempted else None,
        # ...and over the whole benchmark, the strict SWE-bench number.
        "resolution_rate_total": round(100 * resolved / len(tasks), 1) if tasks else None,
        "outcomes": dict(outcomes),
        "average_runtime_seconds": round(sum(runtimes) / len(runtimes), 1) if runtimes else None,
        "environment_validated": sum(1 for r in gold.values() if r["outcome"] == RESOLVED),
        "environment_checked": len(gold),
        "environment_invalid": sorted(invalid),
        "last_provider": last.get("provider") if last else None,
        "last_model": last.get("model") if last else None,
        "dataset": "SWE-bench Lite (subset)",
        "by_model": by_model(tasks, store),
    }
