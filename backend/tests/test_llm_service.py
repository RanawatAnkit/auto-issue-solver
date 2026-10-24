import json
import pytest
import httpx
from unittest.mock import patch, MagicMock
from app.services.llm_service import LLMService, verify_ollama_available
from app.agents.schemas import IssueAnalysis, SolutionPlan, FailureAnalysis
from app.agents.issue_analyzer import IssueAnalyzer
from app.agents.planner import PlannerAgent

# ===========================================================================
# 1. Ollama Provider Selection & Model Configuration
# ===========================================================================

@patch("app.services.llm_service.settings")
def test_ollama_provider_selected(mock_settings):
    """Verifies that LLM_PROVIDER=ollama correctly selects Ollama as active provider."""
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
    mock_settings.OLLAMA_TIMEOUT = 120
    mock_settings.MAX_LLM_RETRIES = 1
    mock_settings.LLM_FALLBACK_PROVIDER = None

    svc = LLMService()
    assert svc.provider == "ollama"
    assert svc.model == "qwen2.5-coder:7b"
    assert svc.primary["is_ollama"] is True
    assert svc.primary["model"] == "qwen2.5-coder:7b"
    assert svc.primary["base_url"] == "http://localhost:11434/v1"
    assert svc.primary["ollama_base"] == "http://localhost:11434"

@patch("app.services.llm_service.settings")
def test_ollama_model_exact_configuration(mock_settings):
    """Ensures exact model name qwen2.5-coder:7b is configured without hardcoding conflicts."""
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://127.0.0.1:11434"
    mock_settings.OLLAMA_TIMEOUT = 90
    mock_settings.LLM_FALLBACK_PROVIDER = None

    cfg = LLMService._build_provider_config("ollama")
    assert cfg["model"] == "qwen2.5-coder:7b"
    assert cfg["name"] == "ollama"
    assert cfg["is_ollama"] is True
    assert cfg["base_url"] == "http://127.0.0.1:11434/v1"

# ===========================================================================
# 2. Structured Output Generation: IssueAnalyzer & PlannerAgent
# ===========================================================================

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_ollama_issue_analysis_structured_success(mock_post, mock_settings):
    """Tests successful structured IssueAnalysis generation via Ollama /api/chat."""
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
    mock_settings.OLLAMA_TIMEOUT = 120
    mock_settings.LLM_FALLBACK_PROVIDER = None
    mock_settings.MAX_LLM_RETRIES = 1

    valid_response_json = {
        "model": "qwen2.5-coder:7b",
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "problem_summary": "ZeroDivisionError in calculate_ratio",
                "expected_behavior": "Return 0.0 or raise graceful ValueError when denominator is 0",
                "current_behavior": "Crashes with ZeroDivisionError",
                "affected_areas": ["math_utils.py"],
                "constraints": ["No external packages", "Preserve existing API signature"]
            })
        },
        "done": True
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = valid_response_json
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    analyzer = IssueAnalyzer()
    res = analyzer.analyze("Bug in ratio", "Division by zero crashes", "bug", "repo/test")

    assert isinstance(res, IssueAnalysis)
    assert res.problem_summary == "ZeroDivisionError in calculate_ratio"
    assert "math_utils.py" in res.affected_areas
    assert len(res.constraints) == 2

    # Verify native /api/chat endpoint was called with format=schema and stream=False
    call_args = mock_post.call_args
    assert call_args[0][0] == "http://localhost:11434/api/chat"
    payload = call_args[1]["json"]
    assert payload["model"] == "qwen2.5-coder:7b"
    assert payload["stream"] is False
    assert "properties" in payload["format"]
    assert "problem_summary" in payload["format"]["properties"]

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_ollama_planner_agent_structured_success(mock_post, mock_settings):
    """Tests successful PlannerAgent structured output validation using Ollama."""
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
    mock_settings.OLLAMA_TIMEOUT = 120
    mock_settings.LLM_FALLBACK_PROVIDER = None
    mock_settings.MAX_LLM_RETRIES = 1

    valid_plan_json = {
        "model": "qwen2.5-coder:7b",
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "summary": "Safely guard against zero denominator in math_utils.py",
                "affected_files": [
                    {
                        "path": "math_utils.py",
                        "operation": "modify",
                        "reason": "Add denominator check",
                        "symbols": ["calculate_ratio"],
                        "planned_changes": ["if b == 0: return 0.0"]
                    }
                ],
                "tests": ["tests/test_math.py"],
                "dependencies": [],
                "risks": ["Low risk of regression"],
                "assumptions": ["Denominator is numeric"],
                "acceptance_criteria": ["calculate_ratio(10, 0) == 0.0 without crash"]
            })
        },
        "done": True
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = valid_plan_json
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    analysis = IssueAnalysis(
        problem_summary="ZeroDivisionError",
        expected_behavior="Handle 0 gracefully",
        current_behavior="Crashes",
        affected_areas=["math_utils.py"],
        constraints=[]
    )

    planner = PlannerAgent()
    plan = planner.create_plan(analysis, [], "repo/test", available_files=["math_utils.py"])

    assert isinstance(plan, SolutionPlan)
    assert plan.summary == "Safely guard against zero denominator in math_utils.py"
    assert len(plan.affected_files) == 1
    assert plan.affected_files[0].path == "math_utils.py"

# ===========================================================================
# 3. Regression: Ollama Echoes Schema Properties Instead of Populated Data
# ===========================================================================

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_ollama_schema_reflection_regression_rejected(mock_post, mock_settings):
    """Detects and rejects the regression where Ollama returns the JSON Schema definition itself."""
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
    mock_settings.OLLAMA_TIMEOUT = 120
    mock_settings.LLM_FALLBACK_PROVIDER = None
    mock_settings.MAX_LLM_RETRIES = 0

    # Model returned the schema structure itself rather than populated values
    schema_echo_response = {
        "model": "qwen2.5-coder:7b",
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "type": "object",
                "properties": {
                    "problem_summary": {"type": "string", "description": "Summary"},
                    "expected_behavior": {"type": "string", "description": "Expected"},
                    "current_behavior": {"type": "string", "description": "Current"},
                    "constraints": {"type": "array", "items": {"type": "string"}}
                }
            })
        },
        "done": True
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = schema_echo_response
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    svc = LLMService()
    with pytest.raises(RuntimeError) as exc_info:
        svc.generate_structured("sys", "user", IssueAnalysis)

    # Asserts that the failure explicitly traces the schema echo or validation issue
    err_str = str(exc_info.value)
    assert "schema" in err_str.lower() or "validation" in err_str.lower()

# ===========================================================================
# 4. Missing Required Fields and Malformed JSON
# ===========================================================================

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_ollama_missing_required_fields_rejected(mock_post, mock_settings):
    """Ensures Pydantic validation rejects responses with missing required fields without fabricating data."""
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
    mock_settings.OLLAMA_TIMEOUT = 120
    mock_settings.LLM_FALLBACK_PROVIDER = None
    mock_settings.MAX_LLM_RETRIES = 0

    incomplete_json = {
        "model": "qwen2.5-coder:7b",
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "problem_summary": "Only problem summary provided"
                # Missing expected_behavior, current_behavior, constraints
            })
        },
        "done": True
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = incomplete_json
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    svc = LLMService()
    with pytest.raises(RuntimeError) as exc_info:
        svc.generate_structured("sys", "user", IssueAnalysis)

    assert "validation" in str(exc_info.value).lower()

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_ollama_malformed_json_rejected(mock_post, mock_settings):
    """Ensures malformed JSON from Ollama is rejected with a clear error."""
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
    mock_settings.OLLAMA_TIMEOUT = 120
    mock_settings.LLM_FALLBACK_PROVIDER = None
    mock_settings.MAX_LLM_RETRIES = 0

    malformed_resp = {
        "model": "qwen2.5-coder:7b",
        "message": {
            "role": "assistant",
            "content": "{ problem_summary: incomplete json missing quotes..."
        },
        "done": True
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = malformed_resp
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    svc = LLMService()
    with pytest.raises(RuntimeError):
        svc.generate_structured("sys", "user", IssueAnalysis)

# ===========================================================================
# 5. Runtime Model Verification: Server Unreachable & Model Missing
# ===========================================================================

@patch("app.services.llm_service.httpx.get")
def test_verify_ollama_unreachable(mock_get):
    """Tests verify_ollama_available when Ollama server is offline."""
    mock_get.side_effect = httpx.ConnectError("Connection refused")

    res = verify_ollama_available("http://localhost:11434", "qwen2.5-coder:7b")
    assert res["available"] is False
    assert "Cannot connect to Ollama" in res["message"]
    assert res["command"] == "ollama serve"

@patch("app.services.llm_service.httpx.get")
def test_verify_ollama_model_missing(mock_get):
    """Tests verify_ollama_available when server is running but model is not installed."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": [{"name": "llama3:8b"}, {"name": "mistral:latest"}]}
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    res = verify_ollama_available("http://localhost:11434", "qwen2.5-coder:7b")
    assert res["available"] is False
    assert "qwen2.5-coder:7b" in res["message"]
    assert res["command"] == "ollama pull qwen2.5-coder:7b"

@patch("app.services.llm_service.httpx.get")
def test_verify_ollama_model_installed(mock_get):
    """Tests verify_ollama_available when model is present."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3:8b"}]}
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    res = verify_ollama_available("http://localhost:11434", "qwen2.5-coder:7b")
    assert res["available"] is True
    assert "available" in res["message"].lower()

# ===========================================================================
# 6. Preservation of Cloud Provider Path & Fallback
# ===========================================================================

@patch("app.services.llm_service.settings")
def test_gemini_provider_selected(mock_settings):
    """Preserves Gemini cloud provider selection."""
    mock_settings.LLM_PROVIDER = "gemini"
    mock_settings.GEMINI_API_KEY = "test_gemini"
    mock_settings.GEMINI_MODEL = "gemini-1.5-pro"
    mock_settings.MAX_LLM_RETRIES = 1
    mock_settings.LLM_FALLBACK_PROVIDER = None

    svc = LLMService()
    assert svc.provider == "gemini"
    assert svc.api_key == "test_gemini"
    assert svc.model == "gemini-1.5-pro"
    assert svc.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"

@patch("app.services.llm_service.settings")
def test_gemini_missing_key_produces_error(mock_settings):
    """Preserves key validation for cloud provider."""
    mock_settings.LLM_PROVIDER = "gemini"
    mock_settings.GEMINI_API_KEY = None
    mock_settings.GEMINI_MODEL = "gemini-1.5-pro"
    mock_settings.MAX_LLM_RETRIES = 1
    mock_settings.LLM_FALLBACK_PROVIDER = None

    svc = LLMService()
    with pytest.raises(ValueError, match="GEMINI_API_KEY not configured"):
        svc.generate_structured("sys", "user", IssueAnalysis)

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_gemini_structured_issue_analysis_returned(mock_openai, mock_settings):
    """Preserves cloud OpenAI SDK structured output parsing."""
    mock_settings.LLM_PROVIDER = "gemini"
    mock_settings.GEMINI_API_KEY = "valid"
    mock_settings.GEMINI_MODEL = "gemini-1.5-pro"
    mock_settings.MAX_LLM_RETRIES = 1
    mock_settings.LLM_FALLBACK_PROVIDER = None

    mock_client = MagicMock()
    mock_openai.return_value = mock_client

    mock_parsed_msg = MagicMock()
    mock_parsed_msg.message.parsed = IssueAnalysis(
        problem_summary="sum",
        current_behavior="cur",
        expected_behavior="exp",
        affected_areas=["file.py"],
        constraints=[]
    )
    mock_client.beta.chat.completions.parse.return_value.choices = [mock_parsed_msg]

    svc = LLMService()
    result = svc.generate_structured("sys", "user", IssueAnalysis)

    mock_openai.assert_called_once_with(api_key="valid", base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    assert result.problem_summary == "sum"

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_gemini_invalid_structured_output_rejected(mock_openai, mock_settings):
    """Preserves retry exhaustion handling on invalid cloud completions."""
    mock_settings.LLM_PROVIDER = "gemini"
    mock_settings.GEMINI_API_KEY = "valid"
    mock_settings.GEMINI_MODEL = "gemini-1.5-pro"
    mock_settings.MAX_LLM_RETRIES = 0
    mock_settings.LLM_FALLBACK_PROVIDER = None

    mock_client = MagicMock()
    mock_openai.return_value = mock_client
    mock_client.beta.chat.completions.parse.side_effect = Exception("ValidationError: missing fields")

    svc = LLMService()
    with pytest.raises(RuntimeError, match="maximum retry bounds exceeded"):
        svc.generate_structured("sys", "user", IssueAnalysis)

# ===========================================================================
# 7. Compatibility with 2-argument and 3-argument Callers
# ===========================================================================

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_generate_structured_two_argument_caller_compatibility(mock_post, mock_settings):
    """Ensures agents calling generate_structured(prompt, ResponseModel) succeed seamlessly."""
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://localhost:11434"
    mock_settings.OLLAMA_TIMEOUT = 120
    mock_settings.LLM_FALLBACK_PROVIDER = None
    mock_settings.MAX_LLM_RETRIES = 1

    failure_analysis_json = {
        "model": "qwen2.5-coder:7b",
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "failure_type": "ASSERTION_ERROR",
                "root_cause": "Expected 2 but got 0",
                "affected_files": ["calc.py"],
                "affected_symbols": ["add"],
                "evidence": ["assert add(1, 1) == 2"],
                "suggested_fix": "Fix return statement in add",
                "confidence": 0.95,
                "unresolved_questions": []
            })
        },
        "done": True
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = failure_analysis_json
    mock_resp.raise_for_status = MagicMock()
    mock_post.return_value = mock_resp

    svc = LLMService()
    # 2-argument invocation as done by FailureAnalyzer / RepairPlanner
    res = svc.generate_structured("Analyze this failure trace...", FailureAnalysis)
    assert isinstance(res, FailureAnalysis)
    assert res.failure_type == "ASSERTION_ERROR"
    assert res.root_cause == "Expected 2 but got 0"


@pytest.mark.parametrize("think, expected", [("", None), ("low", "low"), ("false", False)])
def test_ollama_reasoning_effort_is_sent_only_when_configured(monkeypatch, think, expected):
    """gpt-oss spent its whole output budget on hidden reasoning; OLLAMA_THINK limits it."""
    from pydantic import BaseModel

    class Ok(BaseModel):
        ok: bool

    monkeypatch.setattr("app.services.llm_service.settings.LLM_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.llm_service.settings.LLM_FALLBACK_PROVIDER", None)
    monkeypatch.setattr("app.services.llm_service.settings.OLLAMA_THINK", think)
    response = MagicMock(status_code=200)
    response.json.return_value = {"message": {"content": '{"ok": true}'}, "done": True}
    with patch("app.services.llm_service.httpx.post", return_value=response) as post:
        assert LLMService().generate_structured("s", "u", Ok).ok
    payload = post.call_args.kwargs["json"]
    assert payload.get("think") == expected and ("think" in payload) == (expected is not None)


def test_vary_raises_ollama_temperature_only_for_that_call(monkeypatch):
    """At temperature 0 gpt-oss repeated its rejected edit verbatim; retries ask for variation."""
    from pydantic import BaseModel

    class Ok(BaseModel):
        ok: bool

    monkeypatch.setattr("app.services.llm_service.settings.LLM_PROVIDER", "ollama")
    monkeypatch.setattr("app.services.llm_service.settings.LLM_FALLBACK_PROVIDER", None)
    response = MagicMock(status_code=200)
    response.json.return_value = {"message": {"content": '{"ok": true}'}, "done": True}
    llm = LLMService()
    with patch("app.services.llm_service.httpx.post", return_value=response) as post:
        llm.generate_structured("s", "u", Ok, vary=True)
        llm.generate_structured("s", "u", Ok)
    temps = [c.kwargs["json"]["options"]["temperature"] for c in post.call_args_list]
    assert temps == [0.6, 0.0]


def test_openai_provider_can_target_compatible_apis(monkeypatch):
    """Groq, OpenRouter etc. speak the OpenAI API at their own base URL."""
    monkeypatch.setattr("app.services.llm_service.settings.OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    assert LLMService._build_provider_config("openai")["base_url"] == "https://api.groq.com/openai/v1"
    monkeypatch.setattr("app.services.llm_service.settings.OPENAI_BASE_URL", None)
    assert LLMService._build_provider_config("openai")["base_url"] is None  # api.openai.com


def test_provider_side_schema_rejection_is_retried_as_bad_output(monkeypatch):
    """Groq answers 400 json_validate_failed when the model's JSON misses the schema: retry, don't give up."""
    import httpx
    import openai
    from pydantic import BaseModel

    class Ok(BaseModel):
        ok: bool

    monkeypatch.setattr("app.services.llm_service.settings.LLM_PROVIDER", "openai")
    monkeypatch.setattr("app.services.llm_service.settings.OPENAI_API_KEY", "k")
    monkeypatch.setattr("app.services.llm_service.settings.MAX_LLM_RETRIES", 1)
    monkeypatch.setattr("app.services.llm_service.settings.LLM_FALLBACK_PROVIDER", None)
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    bad = openai.BadRequestError(
        "Generated JSON does not match the expected schema", response=httpx.Response(400, request=request),
        body={"error": {"code": "json_validate_failed"}})
    good = MagicMock()
    good.choices = [MagicMock(message=MagicMock(parsed=Ok(ok=True)))]
    client = MagicMock()
    client.beta.chat.completions.parse.side_effect = [bad, good]
    with patch("app.services.llm_service.openai.OpenAI", return_value=client):
        assert LLMService().generate_structured("s", "u", Ok).ok
    assert client.beta.chat.completions.parse.call_count == 2
