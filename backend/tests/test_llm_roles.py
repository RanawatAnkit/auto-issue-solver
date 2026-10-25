"""Per-purpose LLM selection (LLM_PROVIDER_<ROLE> / LLM_MODEL_<ROLE>)."""
import pytest
from unittest.mock import patch
from app.services.llm_service import LLMService, resolve_llm_role, describe_llm_roles, LLM_ROLES

BASE = dict(
    LLM_PROVIDER="ollama", OLLAMA_MODEL="qwen2.5-coder:7b", OLLAMA_BASE_URL="http://localhost:11434",
    OPENROUTER_API_KEY="or-key", OPENROUTER_MODEL="cohere/north-mini-code:free",
    OPENROUTER_BASE_URL="https://openrouter.ai/api/v1", GEMINI_API_KEY="g", GEMINI_MODEL="gemini-x",
    OPENAI_API_KEY=None, OPENAI_MODEL="gpt-4o", LLM_FALLBACK_PROVIDER=None, MAX_LLM_RETRIES=1,
    **{f"LLM_PROVIDER_{r.upper()}": "" for r in LLM_ROLES}, **{f"LLM_MODEL_{r.upper()}": "" for r in LLM_ROLES},
)


@pytest.fixture
def cfg(monkeypatch):
    def apply(**overrides):
        for k, v in {**BASE, **overrides}.items():
            monkeypatch.setattr(f"app.config.settings.{k}", v, raising=False)
    apply()
    return apply


def test_unset_roles_use_the_global_provider_and_model(cfg):
    for role in LLM_ROLES:
        svc = LLMService(role)
        assert (svc.provider, svc.model) == ("ollama", "qwen2.5-coder:7b")


def test_role_can_switch_provider_and_uses_that_providers_model(cfg):
    cfg(LLM_PROVIDER_CODING="openrouter")
    coding, analysis = LLMService("coding"), LLMService("analysis")
    assert (coding.provider, coding.model) == ("openrouter", "cohere/north-mini-code:free")
    assert (analysis.provider, analysis.model) == ("ollama", "qwen2.5-coder:7b")


def test_role_model_override(cfg):
    cfg(LLM_PROVIDER_PLANNING="openrouter", LLM_MODEL_PLANNING="qwen/qwen3.8-27b",
        LLM_MODEL_REPAIR="qwen2.5-coder:14b")  # model only: stays on the global provider
    assert resolve_llm_role("planning") == ("openrouter", "qwen/qwen3.8-27b")
    assert LLMService("planning").model == "qwen/qwen3.8-27b"
    repair = LLMService("repair")
    assert (repair.provider, repair.model) == ("ollama", "qwen2.5-coder:14b")


def test_whitespace_and_case_are_normalised(cfg):
    cfg(LLM_PROVIDER_CODING="  OpenRouter ", LLM_MODEL_CODING=" qwen/qwen3.8-27b ")
    assert resolve_llm_role("coding") == ("openrouter", "qwen/qwen3.8-27b")


def test_unknown_role_and_provider_are_reported(cfg):
    with pytest.raises(ValueError, match="Unknown LLM role"):
        LLMService("testing")
    cfg(LLM_PROVIDER_ANALYSIS="anthropic")
    with pytest.raises(NotImplementedError, match="openrouter"):
        LLMService("analysis")


def test_each_agent_uses_its_role(cfg):
    from app.agents.issue_analyzer import IssueAnalyzer
    from app.agents.planner import PlannerAgent
    from app.agents.coding_agent import CodingAgent
    from app.agents.failure_analyzer import FailureAnalyzer
    from app.agents.repair_planner import RepairPlanner
    cfg(LLM_MODEL_ANALYSIS="m-analysis", LLM_MODEL_PLANNING="m-planning", LLM_MODEL_CODING="m-coding",
        LLM_MODEL_REPAIR="m-repair")
    assert IssueAnalyzer().llm.model == "m-analysis"
    assert PlannerAgent().llm.model == "m-planning"
    assert CodingAgent().llm.model == "m-coding"
    assert FailureAnalyzer().llm.model == "m-repair"
    assert RepairPlanner().llm.model == "m-repair"


def test_describe_roles_and_health_endpoint(cfg, client):
    cfg(LLM_PROVIDER_CODING="openrouter", LLM_MODEL_CODING="qwen/qwen3.8-27b")
    roles = describe_llm_roles()
    assert roles["coding"] == {"provider": "openrouter", "model": "qwen/qwen3.8-27b"}
    assert roles["analysis"] == {"provider": "ollama", "model": "qwen2.5-coder:7b"}
    with patch("app.services.llm_service.verify_ollama_available",
               return_value={"available": True, "message": "ok", "command": None}) as verify:
        checks = client.get("/health/dependencies").json()["checks"]
    assert checks["llm_coding"] == {"ok": True, "provider": "openrouter", "model": "qwen/qwen3.8-27b",
                                    "message": "API key configured"}
    assert checks["llm_analysis"]["provider"] == "ollama"
    verify.assert_called_once_with(model="qwen2.5-coder:7b")  # each Ollama model checked once


def test_localizer_uses_its_own_role(cfg):
    from unittest.mock import MagicMock
    from app.agents.localizer import Localizer
    from app.agents.schemas import IssueAnalysis
    cfg(LLM_PROVIDER_LOCALIZATION="gemini", LLM_MODEL_LOCALIZATION="gemini-loc")
    with patch("app.agents.localizer.LLMService") as llm_cls:
        llm_cls.return_value.prompt_budget_chars.side_effect = RuntimeError("stop after construction")
        Localizer(MagicMock()).localize("t", "b", IssueAnalysis(problem_summary="p", expected_behavior="e",
                                                               current_behavior="c"))
    llm_cls.assert_called_once_with("localization")
    assert resolve_llm_role("localization") == ("gemini", "gemini-loc")


def test_eval_results_record_every_stage_model_when_they_differ(cfg):
    from app.eval.harness import llm_identity
    assert llm_identity() == ("ollama", "qwen2.5-coder:7b")
    cfg(LLM_PROVIDER_PLANNING="gemini")
    provider, model = llm_identity()
    assert provider == "mixed" and "planning=gemini/gemini-x" in model and "analysis=ollama/qwen2.5-coder:7b" in model
