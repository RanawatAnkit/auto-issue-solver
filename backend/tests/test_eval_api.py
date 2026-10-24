import threading

import pytest

from app.api.auth import get_current_user
from app.eval import harness, jobs
from app.eval.store import ResultsStore
from app.main import app
from app.models.domain import User


class _GatedHarness:
    """Records what it was asked to do and blocks until released, like a long run."""

    def __init__(self, gate, allow_network=False):
        self.gate, self.allow_network = gate, allow_network

    def split_runnable(self, tasks):
        return harness.Harness(allow_network=self.allow_network).split_runnable(tasks)

    def evaluate_many(self, tasks, mode, run_id, on_result, should_stop):
        self.gate.wait(5)
        for t in tasks:
            on_result(harness.TaskResult(run_id=run_id, instance_id=t["instance_id"], repo=t["repo"],
                                         mode=mode, outcome=harness.RESOLVED))
        return [], None


@pytest.fixture
def eval_env(tmp_path, monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: User(id=1)
    gate = threading.Event()
    store = ResultsStore(str(tmp_path))
    runner = jobs.EvalJobRunner(store_factory=lambda: store,
                                harness_factory=lambda allow_network: _GatedHarness(gate, allow_network))
    monkeypatch.setattr(jobs, "runner", runner)
    monkeypatch.setattr("app.api.evaluation._store", lambda: store)
    yield runner, gate, store
    gate.set()


def test_run_starts_background_job_and_rejects_a_second_one(client, eval_env):
    runner, gate, store = eval_env
    first = next(t["instance_id"] for t in harness.load_tasks() if t["repo"] == "pallets/flask")
    res = client.post("/evaluation/run", json={"task_ids": [first], "mode": "gold"})
    assert res.status_code == 202
    assert res.json()["running"] is True and res.json()["mode"] == "gold"

    assert client.post("/evaluation/run", json={}).status_code == 409

    gate.set()
    runner._thread.join(5)
    status = client.get("/evaluation/status").json()
    assert status["running"] is False and status["done"] == 1 and status["outcomes"] == {"RESOLVED": 1}
    assert store.latest(harness.GOLD)[first]["outcome"] == "RESOLVED"


def test_network_tasks_are_skipped_not_recorded_without_network(client, eval_env):
    runner, gate, store = eval_env
    requests_task = next(t["instance_id"] for t in harness.load_tasks() if t["repo"] == "psf/requests")
    flask_task = next(t["instance_id"] for t in harness.load_tasks() if t["repo"] == "pallets/flask")
    res = client.post("/evaluation/run", json={"task_ids": [requests_task, flask_task], "mode": "gold"})
    assert res.json()["skipped"] == [requests_task] and res.json()["total"] == 1
    gate.set()
    runner._thread.join(5)
    assert requests_task not in store.latest(harness.GOLD)  # an earlier real result would survive


def test_unknown_task_ids_are_rejected(client, eval_env):
    res = client.post("/evaluation/run", json={"task_ids": ["nope__nope-1"]})
    assert res.status_code == 400


def test_tasks_and_stats_reflect_recorded_results(client, eval_env):
    _, _, store = eval_env
    task = harness.load_tasks()[0]
    store.append(harness.TaskResult(run_id="r1", instance_id=task["instance_id"], repo=task["repo"],
                                    mode=harness.AGENT, outcome=harness.LLM_ERROR, detail="429"))
    tasks = client.get("/evaluation/tasks").json()
    entry = next(t for t in tasks if t["instance_id"] == task["instance_id"])
    assert entry["latest_result"]["outcome"] == "LLM_ERROR" and entry["environment_check"] is None

    stats = client.get("/evaluation/stats").json()
    # An LLM outage is not an agent attempt.
    assert stats["evaluated"] == 1 and stats["attempted"] == 0 and stats["resolution_rate_attempted"] is None
    assert client.get("/evaluation/history").json()[0]["detail"] == "429"
