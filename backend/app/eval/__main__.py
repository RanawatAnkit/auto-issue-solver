"""Command-line entry point for the SWE-bench harness.

    python -m app.eval list
    python -m app.eval validate [IDS...] [--repo R] [--allow-network]   # gold patches: is the env sound?
    python -m app.eval run      [IDS...] [--repo R] [--allow-network]   # the agent
    python -m app.eval report
"""
import argparse
import logging
import sys

from app.config import settings
from app.eval.harness import AGENT, GOLD, Harness, load_tasks, new_run_id
from app.eval.store import ResultsStore, pending_tasks, summarize


def _select(tasks: list[dict], ids: list[str], repo: str | None) -> list[dict]:
    if ids:
        unknown = sorted(set(ids) - {t["instance_id"] for t in tasks})
        if unknown:
            raise SystemExit(f"Unknown instance id(s): {', '.join(unknown)}")
        tasks = [t for t in tasks if t["instance_id"] in ids]
    if repo:
        tasks = [t for t in tasks if t["repo"] == repo]
    if not tasks:
        raise SystemExit("No tasks selected.")
    return tasks


def _print_summary(tasks: list[dict], store: ResultsStore) -> None:
    s = summarize(tasks, store)
    print(f"\nTasks: {s['total_tasks']}  evaluated: {s['evaluated']}  attempted: {s['attempted']}  "
          f"resolved: {s['resolved']}")
    if s["resolution_rate_total"] is not None:
        attempted = f"{s['resolution_rate_attempted']}%" if s["resolution_rate_attempted"] is not None else "n/a"
        print(f"Resolution rate: {s['resolution_rate_total']}% of all tasks, {attempted} of attempted tasks")
    print("Outcomes: " + (", ".join(f"{k}={v}" for k, v in sorted(s["outcomes"].items())) or "none yet"))
    print(f"Environments validated with gold patches: {s['environment_validated']}/{s['environment_checked']} checked")
    if s["by_model"]:
        print("\nPer model (latest result of each model on each task it ran):")
        for m in s["by_model"]:
            print(f"  {m['model']:<24} resolved {m['resolved']}/{m['attempted']} attempted ({m['evaluated']} run)"
                  + (f": {', '.join(m['resolved_ids'])}" if m["resolved_ids"] else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.eval", description="SWE-bench harness for Auto-Solver")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list benchmark tasks with their latest results")
    sub.add_parser("report", help="print headline numbers")
    for name, help_text in (("run", "run the agent and grade its patches"),
                            ("validate", "grade the reference (gold) patches to check each environment")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("ids", nargs="*", help="instance ids (default: all)")
        p.add_argument("--repo", help="only tasks from this repository, e.g. pallets/flask")
        p.add_argument("--allow-network", action="store_true",
                       help="let tests that need external services (old psf/requests) use the network")
        p.add_argument("--timeout", type=int, help="test timeout in seconds per task")
    sub.choices["run"].add_argument(
        "--pending", action="store_true",
        help="only tasks without an agent attempt yet (never run, or stopped by an LLM/environment error)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    tasks = load_tasks()
    store = ResultsStore(settings.EVAL_RESULTS_DIR)

    if args.command == "list":
        agent, gold = store.latest(AGENT), store.latest(GOLD)
        for t in tasks:
            a, g = agent.get(t["instance_id"]), gold.get(t["instance_id"])
            print(f"{t['instance_id']:<34} {t['version']:<6} env={g['outcome'] if g else '-':<12} "
                  f"agent={a['outcome'] if a else '-'}")
        return 0
    if args.command == "report":
        _print_summary(tasks, store)
        return 0

    selected = _select(tasks, args.ids, args.repo)
    mode = GOLD if args.command == "validate" else AGENT
    if getattr(args, "pending", False):
        selected = pending_tasks(selected, store)
        print(f"{len(selected)} task(s) still pending")
        if not selected:
            return 0
    harness = Harness(allow_network=args.allow_network, test_timeout=args.timeout)
    selected, skipped = harness.split_runnable(selected)
    if skipped:
        print(f"Skipping {len(skipped)} task(s) whose tests need network (use --allow-network): {', '.join(skipped)}")
    if not selected:
        return 0
    run_id = new_run_id()
    print(f"Run {run_id}: {len(selected)} task(s), mode={mode}, results in {settings.EVAL_RESULTS_DIR}")

    def report(result):
        store.append(result)
        print(f"  {result.instance_id:<34} {result.outcome:<18} {result.runtime_seconds:>7.1f}s  {result.detail[:150]}",
              flush=True)

    _, stopped = harness.evaluate_many(selected, mode=mode, run_id=run_id, on_result=report)
    if stopped:
        print(f"\nBatch stopped early. {stopped}")
    _print_summary(tasks, store)
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
