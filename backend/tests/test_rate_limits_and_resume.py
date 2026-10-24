"""Short (per-minute) rate limits are waited out; daily quotas fail fast; runs can resume."""
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from app.eval import harness
from app.eval.store import ResultsStore, pending_tasks
from app.services.llm_service import LLMService, retry_delay_seconds


class _Answer(BaseModel):
    ok: bool


class _RateLimit(Exception):
    status_code = 429

    def __init__(self, delay: str):
        super().__init__(f"You exceeded your current quota ... 'retryDelay': '{delay}'")


def test_retry_delay_parsing():
    assert retry_delay_seconds(_RateLimit("21s")) == 21
    assert retry_delay_seconds(Exception("Please retry in 13h7m13.5s.")) == pytest.approx(47233.5)
    assert retry_delay_seconds(Exception("Internal error")) is None


def _service(monkeypatch, max_retries=0):
    monkeypatch.setattr("app.services.llm_service.settings.LLM_PROVIDER", "openai")
    monkeypatch.setattr("app.services.llm_service.settings.OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("app.services.llm_service.settings.MAX_LLM_RETRIES", max_retries)
    monkeypatch.setattr("app.services.llm_service.settings.LLM_FALLBACK_PROVIDER", None)
    return LLMService()


def test_short_rate_limit_is_waited_out_without_spending_an_attempt(monkeypatch):
    llm = _service(monkeypatch, max_retries=0)  # a single attempt
    calls = iter([_RateLimit("2s"), _Answer(ok=True)])

    def fake_call(*args, **kwargs):
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    with patch.object(llm, "_call_cloud", side_effect=fake_call), \
            patch("app.services.llm_service.time.sleep") as sleep:
        assert llm.generate_structured("s", "u", _Answer).ok
    sleep.assert_called_once_with(3)


def test_daily_quota_fails_immediately(monkeypatch):
    llm = _service(monkeypatch, max_retries=2)
    with patch.object(llm, "_call_cloud", side_effect=_RateLimit("47233s")) as call, \
            patch("app.services.llm_service.time.sleep") as sleep:
        with pytest.raises(_RateLimit):
            llm.generate_structured("s", "u", _Answer)
    assert call.call_count == 1 and not sleep.called


def test_pending_tasks_resume_after_infrastructure_failures(tmp_path):
    store = ResultsStore(str(tmp_path))
    tasks = [{"instance_id": i} for i in ("done", "unresolved", "quota", "never")]
    for iid, outcome in (("done", harness.RESOLVED), ("unresolved", harness.UNRESOLVED),
                         ("quota", harness.LLM_ERROR)):
        store.append(harness.TaskResult(run_id="r", instance_id=iid, repo="x", mode=harness.AGENT, outcome=outcome))
    assert [t["instance_id"] for t in pending_tasks(tasks, store)] == ["quota", "never"]
