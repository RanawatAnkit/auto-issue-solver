"""OpenRouter provider (LLM_PROVIDER=openrouter); the HTTP client is mocked, nothing is sent."""
import json
import httpx
import openai
import pytest
from unittest.mock import patch, MagicMock
from app.services.llm_service import LLMService
from app.agents.schemas import IssueAnalysis

VALID = {"problem_summary": "p", "expected_behavior": "e", "current_behavior": "c"}


def _settings(mock_settings, **overrides):
    values = dict(LLM_PROVIDER="openrouter", OPENROUTER_API_KEY="or-key", OPENROUTER_MODEL="cohere/north-mini-code:free",
                  OPENROUTER_BASE_URL="https://openrouter.ai/api/v1", OPENROUTER_TIMEOUT=300,
                  OPENROUTER_MAX_TOKENS=4096, OPENROUTER_REASONING_TOKENS=8192, OPENROUTER_REASONING_TOKENS_MAX=32768, OPENROUTER_CONTEXT_TOKENS=32000,
                  MAX_LLM_RETRIES=1,
                  LLM_FALLBACK_PROVIDER=None, LLM_ALLOW_PAID_FALLBACK=False, LLM_FALLBACK_ON="quota")
    values.update(overrides)
    for k, v in values.items():
        setattr(mock_settings, k, v)


def _reply(content, finish_reason="stop"):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content), finish_reason=finish_reason)]
    return resp


def _bad_request(message):
    return openai.BadRequestError(message, response=httpx.Response(400, request=httpx.Request("POST", "http://x")), body=None)


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
@pytest.mark.parametrize("model", ["cohere/north-mini-code:free", "qwen/qwen3.8-27b"])
def test_openrouter_model_a_and_b(mock_openai, mock_settings, model):
    _settings(mock_settings, OPENROUTER_MODEL=model)
    mock_openai.return_value.chat.completions.create.return_value = _reply(json.dumps(VALID))
    svc = LLMService()
    assert svc.provider == "openrouter" and svc.model == model
    assert svc.generate_structured("sys", "user", IssueAnalysis).problem_summary == "p"
    client_kwargs = mock_openai.call_args.kwargs
    assert client_kwargs["base_url"] == "https://openrouter.ai/api/v1" and client_kwargs["api_key"] == "or-key"
    call = mock_openai.return_value.chat.completions.create.call_args.kwargs
    assert call["model"] == model
    assert call["response_format"]["type"] == "json_schema"
    assert "problem_summary" in call["messages"][0]["content"]  # schema is in the prompt too


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_qwen_style_reasoning_and_fences_are_stripped(mock_openai, mock_settings):
    _settings(mock_settings, OPENROUTER_MODEL="qwen/qwen3.8-27b")
    content = "<think>Let me work out the fields...</think>\n```json\n" + json.dumps(VALID) + "\n```"
    mock_openai.return_value.chat.completions.create.return_value = _reply(content)
    assert LLMService().generate_structured("sys", "user", IssueAnalysis).current_behavior == "c"


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_model_without_structured_output_support_falls_back_to_json_object(mock_openai, mock_settings):
    _settings(mock_settings)
    create = mock_openai.return_value.chat.completions.create
    create.side_effect = [_bad_request("response_format json_schema is not supported by this model"),
                          _reply(json.dumps(VALID))]
    svc = LLMService()
    assert svc.generate_structured("sys", "user", IssueAnalysis).problem_summary == "p"
    assert create.call_args_list[1].kwargs["response_format"] == {"type": "json_object"}
    # The accepted format is remembered for later calls.
    create.side_effect = None
    create.return_value = _reply(json.dumps(VALID))
    svc.generate_structured("sys", "user", IssueAnalysis)
    assert create.call_args.kwargs["response_format"] == {"type": "json_object"}


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_invalid_json_is_retried_with_feedback(mock_openai, mock_settings):
    _settings(mock_settings)
    create = mock_openai.return_value.chat.completions.create
    create.side_effect = [_reply('{"expected_behavior": "e"}'), _reply(json.dumps(VALID))]
    assert LLMService().generate_structured("sys", "user", IssueAnalysis).problem_summary == "p"
    assert "problem_summary" in create.call_args_list[1].kwargs["messages"][-1]["content"]


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_output_cap_and_retry_temperature_are_passed(mock_openai, mock_settings):
    _settings(mock_settings)
    mock_openai.return_value.chat.completions.create.return_value = _reply(json.dumps(VALID))
    LLMService().generate_structured("sys", "user", IssueAnalysis, max_output_tokens=2048, temperature=0.4)
    call = mock_openai.return_value.chat.completions.create.call_args.kwargs
    # The answer cap gets a reasoning allowance on top (reasoning tokens share the budget).
    assert call["max_tokens"] == 2048 + 8192 and call["temperature"] == 0.4


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_truncated_answer_is_detected(mock_openai, mock_settings):
    _settings(mock_settings, MAX_LLM_RETRIES=0)
    mock_openai.return_value.chat.completions.create.return_value = _reply('{"problem_summary": "p", "exp', "length")
    with pytest.raises(RuntimeError, match="cut off"):
        LLMService().generate_structured("sys", "user", IssueAnalysis)


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_unknown_model_gives_actionable_error(mock_openai, mock_settings):
    _settings(mock_settings, OPENROUTER_MODEL="nobody/nothing")
    mock_openai.return_value.chat.completions.create.side_effect = _bad_request("nobody/nothing is not a valid model ID")
    with pytest.raises(RuntimeError, match="OPENROUTER_MODEL"):
        LLMService().generate_structured("sys", "user", IssueAnalysis)
    assert mock_openai.return_value.chat.completions.create.call_count == 1  # not retried


@patch("app.services.llm_service.settings")
def test_missing_key_is_reported(mock_settings):
    _settings(mock_settings, OPENROUTER_API_KEY=None)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY not configured"):
        LLMService().generate_structured("sys", "user", IssueAnalysis)


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.httpx.post")
@patch("app.services.llm_service.openai.OpenAI")
def test_ollama_never_silently_falls_back_to_openrouter(mock_openai, mock_post, mock_settings, monkeypatch):
    _settings(mock_settings, LLM_PROVIDER="ollama", OLLAMA_MODEL="qwen2.5-coder:7b", OLLAMA_BASE_URL="http://localhost:11434",
              OLLAMA_TIMEOUT=60, OLLAMA_NUM_CTX=16384, OLLAMA_NUM_PREDICT=2048, MAX_LLM_RETRIES=0,
              LLM_FALLBACK_PROVIDER="openrouter", LLM_FALLBACK_ON="unavailable", LLM_ALLOW_PAID_FALLBACK=False)
    monkeypatch.setattr("app.services.llm_service.time.sleep", lambda s: None)
    mock_post.side_effect = httpx.ConnectError("refused")
    with pytest.raises(RuntimeError):
        LLMService().generate_structured("sys", "user", IssueAnalysis)
    mock_openai.return_value.chat.completions.create.assert_not_called()


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_reasoning_cutoff_retries_with_more_room_and_remembers_it(mock_openai, mock_settings):
    # Latest failure: 13,202 reasoning tokens against a 10,240-token limit.
    _settings(mock_settings, MAX_LLM_RETRIES=0)
    cut = _reply("", "length")
    cut.usage.completion_tokens_details.reasoning_tokens = 13202
    create = mock_openai.return_value.chat.completions.create
    create.side_effect = [cut, _reply(json.dumps(VALID))]
    svc = LLMService()
    assert svc.generate_structured("sys", "user", IssueAnalysis, max_output_tokens=2048).problem_summary == "p"
    limits = [c.kwargs["max_tokens"] for c in create.call_args_list]
    assert limits == [2048 + 8192, 2048 + 16384]
    # The bigger allowance is kept for the next call instead of failing first again.
    create.side_effect = None
    create.return_value = _reply(json.dumps(VALID))
    svc.generate_structured("sys", "user", IssueAnalysis, max_output_tokens=2048)
    assert create.call_args.kwargs["max_tokens"] == 2048 + 16384


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_reasoning_beyond_the_ceiling_is_explained(mock_openai, mock_settings):
    _settings(mock_settings, MAX_LLM_RETRIES=0)
    cut = _reply("", "length")
    cut.usage.completion_tokens_details.reasoning_tokens = 40000
    mock_openai.return_value.chat.completions.create.return_value = cut
    with pytest.raises(RuntimeError, match="even with the maximum allowance"):
        LLMService().generate_structured("sys", "user", IssueAnalysis, max_output_tokens=2048)
    limits = [c.kwargs["max_tokens"] for c in mock_openai.return_value.chat.completions.create.call_args_list]
    assert limits == [2048 + 8192, 2048 + 16384, 2048 + 32768]  # doubles, then stops at the ceiling


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_long_answer_without_reasoning_is_not_retried_with_more_reasoning(mock_openai, mock_settings):
    _settings(mock_settings, MAX_LLM_RETRIES=0)
    cut = _reply('{"problem_summary": "' + "x" * 500, "length")
    cut.usage.completion_tokens_details.reasoning_tokens = 0
    mock_openai.return_value.chat.completions.create.return_value = cut
    with pytest.raises(RuntimeError, match="answer itself was too long"):
        LLMService().generate_structured("sys", "user", IssueAnalysis, max_output_tokens=2048)
    assert mock_openai.return_value.chat.completions.create.call_count == 1


@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_runs_311_312_empty_answer_retry_sends_no_empty_message(mock_openai, mock_settings):
    # Cohere answered with empty content; the retry used to quote it back as an empty
    # assistant message, which Cohere rejects with HTTP 400.
    _settings(mock_settings, MAX_LLM_RETRIES=1)
    create = mock_openai.return_value.chat.completions.create
    create.side_effect = [_reply("", "stop"), _reply(json.dumps(VALID))]
    assert LLMService().generate_structured("sys", "user", IssueAnalysis).problem_summary == "p"
    retry_messages = create.call_args_list[1].kwargs["messages"]
    assert all(m["content"].strip() for m in retry_messages)
    assert [m["role"] for m in retry_messages] == ["system", "user", "user"]
    assert "contained no text" in retry_messages[-1]["content"]
