"""Regression tests for LLM structured-output reliability, retries and fallback."""
import json
import pytest
import httpx
import openai
from unittest.mock import patch, MagicMock
from app.services.llm_service import LLMService, parse_structured_output, LLMOutputError
from app.agents.schemas import IssueAnalysis

VALID = {"problem_summary": "p", "expected_behavior": "e", "current_behavior": "c"}


def _settings(mock_settings, **overrides):
    values = dict(LLM_PROVIDER="ollama", OLLAMA_MODEL="qwen2.5-coder:7b", OLLAMA_BASE_URL="http://localhost:11434",
                  OLLAMA_TIMEOUT=60, OLLAMA_NUM_CTX=16384, OLLAMA_NUM_PREDICT=2048, MAX_LLM_RETRIES=2,
                  LLM_FALLBACK_PROVIDER=None, LLM_ALLOW_PAID_FALLBACK=False, LLM_FALLBACK_ON="quota",
                  GEMINI_API_KEY="g-key", GEMINI_MODEL="gemini-x", OPENAI_API_KEY=None, MAX_CONTEXT_TOKENS=12000)
    values.update(overrides)
    for k, v in values.items():
        setattr(mock_settings, k, v)


def _ollama_reply(content, **extra):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"message": {"content": content}, "done": True, **extra}
    return resp


@pytest.mark.parametrize("raw", [
    json.dumps(VALID),
    "```json\n" + json.dumps(VALID) + "\n```",
    "Here is the analysis:\n" + json.dumps(VALID) + "\nHope this helps!",
])
def test_parse_accepts_fenced_and_wrapped_json(raw):
    assert parse_structured_output(raw, IssueAnalysis).problem_summary == "p"


def test_parse_reports_missing_field_by_name():
    with pytest.raises(LLMOutputError, match="problem_summary"):
        parse_structured_output(json.dumps({"expected_behavior": "e", "current_behavior": "c"}), IssueAnalysis)


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_ollama_request_sets_context_window(mock_post, mock_settings):
    # Regression: without num_ctx Ollama used a 2048-token window and silently dropped
    # the start of long prompts (the system instructions), producing invalid output.
    _settings(mock_settings, OLLAMA_NUM_CTX=16384, OLLAMA_NUM_PREDICT=2048)
    mock_post.return_value = _ollama_reply(json.dumps(VALID))
    LLMService().generate_structured("sys", "user", IssueAnalysis)
    options = mock_post.call_args.kwargs["json"]["options"]
    assert options["num_ctx"] == 16384 and options["num_predict"] == 2048 and options["temperature"] == 0


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_invalid_output_is_retried_with_validation_feedback(mock_post, mock_settings):
    _settings(mock_settings, MAX_LLM_RETRIES=1)
    mock_post.side_effect = [_ollama_reply(json.dumps({"expected_behavior": "e"})), _ollama_reply(json.dumps(VALID))]
    result = LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert result.problem_summary == "p"
    retry_messages = mock_post.call_args_list[1].kwargs["json"]["messages"]
    assert "problem_summary" in retry_messages[-1]["content"]  # the model is told what was wrong
    assert retry_messages[-2]["role"] == "assistant"


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_prompt_larger_than_context_is_refused_not_truncated(mock_post, mock_settings):
    _settings(mock_settings, OLLAMA_NUM_CTX=2048, OLLAMA_NUM_PREDICT=512)
    with pytest.raises(RuntimeError, match="Prompt too large"):
        LLMService().generate_structured("sys", "x" * 20000, IssueAnalysis)
    mock_post.assert_not_called()


@patch("app.services.llm_service.time.sleep")
@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_timeouts_are_retried_at_most_once(mock_post, mock_settings, _sleep):
    _settings(mock_settings, MAX_LLM_RETRIES=5)
    mock_post.side_effect = httpx.ReadTimeout("slow")
    with pytest.raises(RuntimeError, match="timed out"):
        LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert mock_post.call_count == 2


@patch("app.services.llm_service.time.sleep")
@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_unreachable_ollama_reports_actionable_error(mock_post, mock_settings, _sleep):
    _settings(mock_settings, MAX_LLM_RETRIES=1)
    mock_post.side_effect = httpx.ConnectError("refused")
    with pytest.raises(RuntimeError, match="ollama serve"):
        LLMService().generate_structured("sys", "user", IssueAnalysis)


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_missing_model_is_not_retried(mock_post, mock_settings):
    _settings(mock_settings, MAX_LLM_RETRIES=3)
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    mock_post.return_value = MagicMock(raise_for_status=MagicMock(side_effect=httpx.HTTPStatusError(
        "404", request=request, response=httpx.Response(404, text='{"error":"model not found"}', request=request))))
    with pytest.raises(RuntimeError, match="ollama pull"):
        LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert mock_post.call_count == 1


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
def test_truncated_generation_is_detected(mock_post, mock_settings):
    _settings(mock_settings, MAX_LLM_RETRIES=0)
    mock_post.return_value = _ollama_reply('{"problem_summary": "p", "expec', done_reason="length")
    with pytest.raises(RuntimeError, match="cut off"):
        LLMService().generate_structured("sys", "user", IssueAnalysis)


def _quota_error():
    return openai.RateLimitError("quota exceeded", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_no_silent_fallback_to_paid_provider(mock_openai, mock_settings):
    _settings(mock_settings, LLM_PROVIDER="gemini", LLM_FALLBACK_PROVIDER="openai", OPENAI_API_KEY="o-key",
              LLM_ALLOW_PAID_FALLBACK=False)
    mock_openai.return_value.beta.chat.completions.parse.side_effect = _quota_error()
    with pytest.raises(openai.RateLimitError):
        LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert mock_openai.call_count == 1  # the fallback client was never created


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
@patch("app.services.llm_service.openai.OpenAI")
def test_fallback_to_local_ollama_on_quota(mock_openai, mock_post, mock_settings):
    _settings(mock_settings, LLM_PROVIDER="gemini", LLM_FALLBACK_PROVIDER="ollama")
    mock_openai.return_value.beta.chat.completions.parse.side_effect = _quota_error()
    mock_post.return_value = _ollama_reply(json.dumps(VALID))
    assert LLMService().generate_structured("sys", "user", IssueAnalysis).problem_summary == "p"


@patch("app.services.llm_service.time.sleep")
@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
@patch("app.services.llm_service.openai.OpenAI")
def test_paid_fallback_when_unavailable_requires_opt_in(mock_openai, mock_post, mock_settings, _sleep):
    _settings(mock_settings, LLM_FALLBACK_PROVIDER="gemini", LLM_FALLBACK_ON="unavailable",
              LLM_ALLOW_PAID_FALLBACK=True, MAX_LLM_RETRIES=0)
    mock_post.side_effect = httpx.ConnectError("refused")
    parsed = IssueAnalysis(**VALID)
    mock_openai.return_value.beta.chat.completions.parse.return_value.choices = [MagicMock(message=MagicMock(parsed=parsed))]
    assert LLMService().generate_structured("sys", "user", IssueAnalysis) == parsed


@patch("app.services.llm_service.time.sleep")
@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_gemini_503_is_retried_then_succeeds(mock_openai, mock_settings, _sleep):
    _settings(mock_settings, LLM_PROVIDER="gemini", MAX_LLM_RETRIES=2)
    unavailable = openai.InternalServerError("503 UNAVAILABLE", response=httpx.Response(503, request=httpx.Request("POST", "http://x")), body=None)
    parsed = IssueAnalysis(**VALID)
    ok = MagicMock(); ok.choices = [MagicMock(message=MagicMock(parsed=parsed))]
    mock_openai.return_value.beta.chat.completions.parse.side_effect = [unavailable, ok]
    assert LLMService().generate_structured("sys", "user", IssueAnalysis) == parsed


@pytest.mark.ollama
def test_live_ollama_issue_analysis():
    """Calls the configured local model (RUN_OLLAMA_TESTS=1)."""
    from app.agents.issue_analyzer import IssueAnalyzer
    res = IssueAnalyzer().analyze(
        "Division by zero in ratio()", "ratio(1, 0) raises ZeroDivisionError; it should return None.",
        "bug", "example/repo", ["calc.py", "tests/test_calc.py"])
    assert res.problem_summary and res.expected_behavior and res.current_behavior


# ---- run 326: Gemini rejected the planner schema with "400 Request contains an invalid argument" ----

def test_gemini_is_sent_the_schema_without_array_counts_but_answers_are_still_validated():
    from app.services.llm_service import _without_array_counts
    from app.agents.schemas import plan_draft_model
    model = plan_draft_model(["a"], [1])
    sent = _without_array_counts(model)
    text = json.dumps(sent.model_json_schema())
    assert "minItems" not in text and "maxItems" not in text
    assert "maxLength" in text and "pattern" in text  # only the rejected keywords are removed
    assert sent.__name__ == model.__name__
    import pydantic
    with pytest.raises(pydantic.ValidationError, match="risks"):
        sent.model_validate({"summary": "s", "root_cause_hypotheses": [], "affected_files": [], "risks": [],
                             "assumptions": [], "dependencies": [], "tests_for_criterion_1": []})


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_gemini_call_uses_the_compatible_schema_and_returns_the_requested_class(mock_openai, mock_settings):
    _settings(mock_settings, LLM_PROVIDER="gemini")
    parse = mock_openai.return_value.beta.chat.completions.parse
    def answer(**kwargs):
        fmt = kwargs["response_format"]
        assert "minItems" not in json.dumps(fmt.model_json_schema())
        return MagicMock(choices=[MagicMock(message=MagicMock(parsed=fmt.model_validate(VALID)))])
    parse.side_effect = answer
    result = LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert type(result) is IssueAnalysis and result.problem_summary == "p"


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_openai_keeps_the_full_schema(mock_openai, mock_settings):
    _settings(mock_settings, LLM_PROVIDER="openai", OPENAI_API_KEY="o-key", OPENAI_MODEL="gpt-x")
    parse = mock_openai.return_value.beta.chat.completions.parse
    parse.return_value = MagicMock(choices=[MagicMock(message=MagicMock(parsed=IssueAnalysis(**VALID)))])
    LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert parse.call_args.kwargs["response_format"] is IssueAnalysis


@patch("app.services.llm_service.time.sleep")
@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_sdk_side_rejection_feedback_does_not_claim_an_empty_answer(mock_openai, mock_settings, _sleep):
    import pydantic
    _settings(mock_settings, LLM_PROVIDER="gemini", MAX_LLM_RETRIES=1)
    try:
        IssueAnalysis.model_validate({})
    except pydantic.ValidationError as err:
        invalid = err
    parse = mock_openai.return_value.beta.chat.completions.parse
    parse.side_effect = [invalid, MagicMock(choices=[MagicMock(message=MagicMock(parsed=IssueAnalysis(**VALID)))])]
    LLMService().generate_structured("sys", "user", IssueAnalysis)
    retry = parse.call_args_list[1].kwargs["messages"][-1]["content"]
    assert "problem_summary" in retry and "contained no text" not in retry
