"""At most one benchmark run at a time inside the API process, in a background thread.

A full run takes hours, so the API starts it and reports progress instead of
holding the request open. State lives in this process only: restarting the API
abandons a running job (results already recorded stay in the history).
"""
import logging
import threading
from datetime import datetime, timezone

from app.config import settings
from app.eval.harness import Harness, TaskResult, new_run_id
from app.eval.store import ResultsStore

logger = logging.getLogger(__name__)


class JobAlreadyRunning(RuntimeError):
    pass


class EvalJobRunner:
    def __init__(self, store_factory=lambda: ResultsStore(settings.EVAL_RESULTS_DIR), harness_factory=Harness):
        self._lock = threading.Lock()
        self._store_factory = store_factory
        self._harness_factory = harness_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state: dict = {"running": False}

    def status(self) -> dict:
        with self._lock:
            return dict(self.state)

    def start(self, tasks: list[dict], mode: str, allow_network: bool) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise JobAlreadyRunning(f"Run {self.state.get('run_id')} is still in progress.")
            run_id = new_run_id()
            self._stop.clear()
            harness = self._harness_factory(allow_network=allow_network)
            tasks, skipped = harness.split_runnable(tasks)
            self.state = {
                "running": True, "run_id": run_id, "mode": mode, "total": len(tasks), "done": 0,
                "current": tasks[0]["instance_id"] if tasks else None,
                "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "finished_at": None, "stopped_reason": None, "error": None,
                "outcomes": {},
                # Need network that this run does not allow; not evaluated, not recorded.
                "skipped": skipped,
            }
            self._thread = threading.Thread(target=self._run, args=(harness, tasks, mode, run_id),
                                            name=f"eval-{run_id}", daemon=True)
            self._thread.start()
            return dict(self.state)

    def request_stop(self) -> None:
        """Finish the task in progress, then stop."""
        self._stop.set()

    def _run(self, harness, tasks, mode, run_id):
        store = self._store_factory()
        remaining = iter(tasks)
        next(remaining, None)

        def record(result: TaskResult):
            store.append(result)
            with self._lock:
                self.state["done"] += 1
                outcomes = self.state["outcomes"]
                outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
                upcoming = next(remaining, None)
                self.state["current"] = upcoming["instance_id"] if upcoming else None

        try:
            _, stopped = harness.evaluate_many(tasks, mode=mode, run_id=run_id, on_result=record,
                                               should_stop=self._stop.is_set)
            with self._lock:
                self.state["stopped_reason"] = stopped
        except Exception as e:
            logger.exception("Evaluation run %s crashed", run_id)
            with self._lock:
                self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self.state.update(running=False, current=None,
                                  finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


runner = EvalJobRunner()
