"""Regenerate app/eval/benchmark_tasks.json from the public SWE-bench Lite dataset.

    python scripts/build_swebench_subset.py

The subset is limited to repositories whose test environments the harness knows
how to build (see app/eval/environments.py). Every instance listed here should
pass gold-patch validation (`python -m app.eval validate`); instances that do not
are kept out of the list rather than counted as agent failures.
"""
import json
import os
import sys
import urllib.request

DATASET = "princeton-nlp/SWE-bench_Lite"
ROWS_URL = "https://datasets-server.huggingface.co/rows?dataset={ds}&config=default&split=test&offset={off}&length=100"
OUT = os.path.join(os.path.dirname(__file__), "..", "app", "eval", "benchmark_tasks.json")

INSTANCE_IDS = [
    # psf/requests (test suites call httpbin.org: need --allow-network)
    "psf__requests-863", "psf__requests-1963", "psf__requests-2148", "psf__requests-2317",
    "psf__requests-2674", "psf__requests-3362",
    # pallets/flask
    "pallets__flask-4045", "pallets__flask-4992", "pallets__flask-5063",
    # pytest-dev/pytest
    "pytest-dev__pytest-5103", "pytest-dev__pytest-5221", "pytest-dev__pytest-5227",
    "pytest-dev__pytest-5413", "pytest-dev__pytest-5495", "pytest-dev__pytest-5692",
    "pytest-dev__pytest-6116", "pytest-dev__pytest-7168", "pytest-dev__pytest-7220",
    "pytest-dev__pytest-7373", "pytest-dev__pytest-7432", "pytest-dev__pytest-7490",
    "pytest-dev__pytest-8365", "pytest-dev__pytest-8906", "pytest-dev__pytest-9359",
    "pytest-dev__pytest-11143", "pytest-dev__pytest-11148",
    # pylint-dev/pylint
    "pylint-dev__pylint-5859", "pylint-dev__pylint-6506", "pylint-dev__pylint-7080",
    "pylint-dev__pylint-7114", "pylint-dev__pylint-7228", "pylint-dev__pylint-7993",
]

FIELDS = ("instance_id", "repo", "version", "base_commit", "environment_setup_commit", "created_at",
          "problem_statement", "hints_text", "patch", "test_patch")


def fetch_rows() -> list[dict]:
    rows, offset = [], 0
    while True:
        url = ROWS_URL.format(ds=DATASET, off=offset)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "auto-issue-solver"}), timeout=60) as r:
            page = json.load(r)
        batch = [item["row"] for item in page["rows"]]
        rows.extend(batch)
        offset += len(batch)
        if not batch or offset >= page.get("num_rows_total", 0):
            return rows


def main() -> int:
    by_id = {row["instance_id"]: row for row in fetch_rows()}
    missing = [i for i in INSTANCE_IDS if i not in by_id]
    if missing:
        print(f"Not found in {DATASET}: {missing}", file=sys.stderr)
        return 1
    tasks = []
    for iid in INSTANCE_IDS:
        row = by_id[iid]
        task = {k: row[k] for k in FIELDS}
        # The dataset stores the test lists as JSON-encoded strings.
        task["FAIL_TO_PASS"] = json.loads(row["FAIL_TO_PASS"])
        task["PASS_TO_PASS"] = json.loads(row["PASS_TO_PASS"])
        tasks.append(task)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(tasks, f, indent=1)
        f.write("\n")
    print(f"Wrote {len(tasks)} tasks to {os.path.normpath(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
