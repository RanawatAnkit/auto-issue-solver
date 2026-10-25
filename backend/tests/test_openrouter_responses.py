"""OpenRouter response handling, modelled on the responses observed from poolside/laguna-s-2.1:free
(run 322): JSON requested, but the model answered with its native tool-call markup, which failed as
"Response is not valid JSON (Expecting value at position 0)". Every API response here is mocked."""
import os
import json
import shutil
import tempfile
import httpx
import openai
import pytest
from unittest.mock import patch, MagicMock
from app.services.llm_service import LLMService
from app.services.symbol_index import SymbolIndex
from app.agents.localizer import Localizer
from app.agents.planner import PlannerAgent
from app.agents.schemas import IssueAnalysis, LocalizationStep, CodeLocation

VALID = {"problem_summary": "p", "expected_behavior": "e", "current_behavior": "c"}
# Abridged from the real run-322 replay: prose followed by Poolside tool-call syntax, no JSON at all.
TOOL_CALL_ANSWER = ("Looking at the issue, I need to understand the full flow: routes -> services. Let me inspect the "
                    "routes.<tool_call>read_lines<arg_key>file_path</arg_key><arg_value>app/routes.py</arg_value>"
                    "<arg_key>start_line</arg_key><arg_value>1</arg_value></tool_call>")


def _settings(mock_settings, **overrides):
    values = dict(LLM_PROVIDER="openrouter", OPENROUTER_API_KEY="or-key", OPENROUTER_MODEL="poolside/laguna-s-2.1:free",
                  OPENROUTER_BASE_URL="https://openrouter.ai/api/v1", OPENROUTER_TIMEOUT=300,
                  OPENROUTER_MAX_TOKENS=4096, OPENROUTER_REASONING_TOKENS=8192, OPENROUTER_REASONING_TOKENS_MAX=32768,
                  OPENROUTER_CONTEXT_TOKENS=32000, MAX_LLM_RETRIES=2,
                  LLM_FALLBACK_PROVIDER=None, LLM_ALLOW_PAID_FALLBACK=False, LLM_FALLBACK_ON="quota")
    values.update(overrides)
    for k, v in values.items():
        setattr(mock_settings, k, v)


def reply(content, finish_reason="stop", reasoning=None, refusal=None, reasoning_tokens=None,
          prompt_tokens=100, completion_tokens=50):
    """A response shaped like OpenRouter's: answer in content, reasoning in a separate field."""
    resp = MagicMock()
    resp.model, resp.provider = "poolside/laguna-s-2.1:free", "Poolside"
    resp.usage.prompt_tokens, resp.usage.completion_tokens = prompt_tokens, completion_tokens
    resp.usage.total_tokens = prompt_tokens + completion_tokens
    resp.usage.completion_tokens_details.reasoning_tokens = reasoning_tokens
    message = MagicMock(content=content, reasoning=reasoning, refusal=refusal)
    resp.choices = [MagicMock(message=message, finish_reason=finish_reason)]
    return resp


@pytest.fixture
def client():
    with patch("app.services.llm_service.settings") as mock_settings, \
         patch("app.services.llm_service.openai.OpenAI") as mock_openai, \
         patch("app.services.llm_service.time.sleep"):
        _settings(mock_settings)
        yield mock_openai.return_value.chat.completions.create, mock_settings


def generate(**kwargs):
    return LLMService().generate_structured("sys", "user", IssueAnalysis, **kwargs)


# 1. valid JSON
def test_valid_json(client):
    create, _ = client
    create.return_value = reply(json.dumps(VALID))
    assert generate().problem_summary == "p"
    assert create.call_count == 1


# 2. empty content with HTTP 200
def test_empty_content_is_retried_then_reported(client):
    create, _ = client
    create.return_value = reply("")
    with pytest.raises(RuntimeError, match="contained no text"):
        generate()
    assert create.call_count == 3  # MAX_LLM_RETRIES=2 -> 3 attempts, no more
    retry = create.call_args_list[1].kwargs["messages"]
    assert all(m["content"].strip() for m in retry)  # the empty answer is never echoed back


# 3. malformed JSON (recoverable within the retry budget)
def test_malformed_json_is_retried_with_the_parse_error(client):
    create, _ = client
    create.side_effect = [reply('{"problem_summary": "p", "expected_behavior": '), reply(json.dumps(VALID))]
    assert generate().problem_summary == "p"
    feedback = create.call_args_list[1].kwargs["messages"][-1]["content"]
    assert "not valid JSON" in feedback and "finish_reason=stop" in feedback


# 4. Markdown-wrapped JSON
def test_markdown_wrapped_json_is_accepted(client):
    create, _ = client
    create.return_value = reply("```json\n" + json.dumps(VALID) + "\n```")
    assert generate().current_behavior == "c"


# 5. token-limit truncation
def test_truncated_answer_reports_the_limit(client):
    create, mock_settings = client
    mock_settings.MAX_LLM_RETRIES = 0
    create.return_value = reply('{"problem_summary": "p", "exp', finish_reason="length", reasoning_tokens=300)
    with pytest.raises(RuntimeError, match=r"answer itself was too long \(reasoning used 300 of its 8192"):
        generate()
    assert create.call_count == 1  # reasoning did not use up its share: no bigger allowance


def test_allowance_grows_only_when_reasoning_used_it_up(client):
    # Run 322: 12,291 reasoning tokens against an 8,192 allowance -> one retry with 16,384.
    create, mock_settings = client
    mock_settings.MAX_LLM_RETRIES = 0
    create.side_effect = [reply("", finish_reason="length", reasoning_tokens=12291), reply(json.dumps(VALID))]
    assert generate(max_output_tokens=2048).problem_summary == "p"
    assert [c.kwargs["max_tokens"] for c in create.call_args_list] == [2048 + 8192, 2048 + 16384]


# 6. refusal and provider errors
def test_refusal_is_reported_and_not_retried(client):
    create, _ = client
    create.return_value = reply(None, refusal="I can't help with that.")
    with pytest.raises(RuntimeError, match="refused: I can't help with that"):
        generate()
    assert create.call_count == 1


def test_provider_error_without_choices_is_retried_as_transient(client):
    create, _ = client
    broken = MagicMock(choices=None, error="upstream overloaded")
    create.side_effect = [broken, reply(json.dumps(VALID))]
    assert generate().problem_summary == "p"
    assert create.call_count == 2


def test_rejected_json_schema_switches_to_json_object_without_claiming_enforcement(client):
    # Observed: Poolside answers HTTP 400 "response_format ... Input should be 'text'".
    create, _ = client
    bad = openai.BadRequestError("response_format.ResponseFormatText.type (json_schema): Input should be 'text'",
                                 response=httpx.Response(400, request=httpx.Request("POST", "http://x")), body=None)
    create.side_effect = [bad, reply("not json at all"), reply(json.dumps(VALID))]
    assert generate().problem_summary == "p"
    assert create.call_args_list[1].kwargs["response_format"] == {"type": "json_object"}
    assert "JSON mode, which this provider may not enforce" in create.call_args_list[2].kwargs["messages"][-1]["content"]


# 7. reasoning present with valid final content
def test_reasoning_with_valid_content_parses_only_the_content(client):
    create, _ = client
    create.return_value = reply(json.dumps(VALID), reasoning="Let me think about {not json} ...", reasoning_tokens=823)
    assert generate().problem_summary == "p"


# 8. reasoning present with empty final content
def test_reasoning_without_an_answer_is_named_as_such(client):
    create, mock_settings = client
    mock_settings.MAX_LLM_RETRIES = 0
    create.return_value = reply("", reasoning="I should look at routes.py first...", reasoning_tokens=40)
    with pytest.raises(RuntimeError, match=r"produced \d+ characters of reasoning but no final answer"):
        generate()


# The run-322 failure itself
def test_tool_call_markup_is_identified_and_corrected(client):
    create, _ = client
    create.side_effect = [reply(TOOL_CALL_ANSWER), reply(json.dumps(VALID))]
    assert generate().problem_summary == "p"
    feedback = create.call_args_list[1].kwargs["messages"][-1]["content"]
    assert "tool-call markup instead of a JSON object" in feedback and "Tools cannot be called here" in feedback


# ---- localization on top of the provider ----

@pytest.fixture
def index():
    root = tempfile.mkdtemp(prefix="orloc-")
    files = {"app/routes.py": "def list_tasks(page=1, page_size=20):\n    return []\n",
             "app/services.py": "class TaskService:\n    def list_tasks(self, page=1, page_size=20):\n        return []\n"}
    for rel, text in files.items():
        os.makedirs(os.path.join(root, os.path.dirname(rel)), exist_ok=True)
        with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
            f.write(text)
    yield SymbolIndex.build(root, list(files))
    shutil.rmtree(root, ignore_errors=True)


ANALYSIS = IssueAnalysis(problem_summary="overdue filter", expected_behavior="e", current_behavior="c")
SEED = CodeLocation(file_path="app/routes.py", symbol="list_tasks", kind="function", start_line=1, end_line=2,
                    evidence=["handles `GET /tasks` named in the issue"])
STEP = LocalizationStep(analysis="TaskService.list_tasks paginates without a due-date filter", done=True,
                        locations=[{"file_path": "app/services.py", "symbol": "TaskService.list_tasks",
                                    "reason": "it filters the task list"}])


# 9. retry exhaustion -> deterministic, unconfirmed fallback
def test_retry_exhaustion_falls_back_to_unconfirmed_seeds(client, index):
    create, _ = client
    create.return_value = reply(TOOL_CALL_ANSWER)
    result = Localizer(index, LLMService()).localize("t", "GET /tasks", ANALYSIS, seeds=[SEED])
    assert create.call_count == 3  # the configured retry budget, nothing more
    assert result.status == "seeded" and [l.symbol for l in result.locations] == ["list_tasks"]
    assert "not confirmed" in result.reason and "LLM call failed in round 1" in result.reason
    assert "tool-call markup" in result.llm_error and "after 3 attempt(s)" in result.llm_error


# 10. successful localization after a recoverable malformed response
def test_localization_succeeds_after_a_tool_call_answer(client, index):
    create, _ = client
    create.side_effect = [reply(TOOL_CALL_ANSWER), reply(STEP.model_dump_json())]
    result = Localizer(index, LLMService()).localize("t", "GET /tasks", ANALYSIS, seeds=[SEED])
    assert result.status == "localized" and [l.symbol for l in result.locations] == ["TaskService.list_tasks"]
    assert result.llm_error == ""
    assert create.call_args_list[0].kwargs["max_tokens"] == 2048 + 8192  # bounded localization answer


def test_localizer_prompt_describes_searches_as_json_fields_not_tools(client, index):
    create, _ = client
    create.return_value = reply(STEP.model_dump_json())
    Localizer(index, LLMService()).localize("t", "b", ANALYSIS, seeds=[SEED])
    user = create.call_args.kwargs["messages"][1]["content"]
    assert "You cannot call tools" in user and "'searches' list of your JSON answer" in user
    assert "Available searches:" not in user


def test_unconfirmed_candidates_are_labelled_and_not_required_in_the_plan(index):
    with patch("app.agents.planner.LLMService") as llm_cls:
        llm = llm_cls.return_value
        llm.prompt_budget_chars.return_value = 60000
        llm.generate_structured.side_effect = RuntimeError("stop after capturing the prompt")
        with pytest.raises(RuntimeError):
            PlannerAgent().create_plan(ANALYSIS, [], "o/r", ["app/routes.py", "app/services.py"], locations=[SEED],
                                       symbol_index=index, locations_confirmed=False)
    system, user = llm.generate_structured.call_args.args[:2]
    assert "Candidate Code Locations" in user and "NOT confirmed" in user
    assert "Localized Code Locations (verified)" not in user
    assert "Every Localized Code Location must appear" not in system
