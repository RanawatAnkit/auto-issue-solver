import pytest
from unittest.mock import patch, MagicMock
from app.services.llm_service import LLMService
from app.agents.schemas import IssueAnalysis
from app.config import Settings

@patch("app.services.llm_service.settings")
def test_gemini_provider_selected(mock_settings):
    mock_settings.LLM_PROVIDER = "gemini"
    mock_settings.GEMINI_API_KEY = "test_gemini"
    mock_settings.GEMINI_MODEL = "gemini-1.5-pro"
    mock_settings.MAX_LLM_RETRIES = 1
    
    svc = LLMService()
    assert svc.provider == "gemini"
    assert svc.api_key == "test_gemini"
    assert svc.model == "gemini-1.5-pro"
    assert svc.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"

@patch("app.services.llm_service.settings")
def test_gemini_missing_key_produces_error(mock_settings):
    mock_settings.LLM_PROVIDER = "gemini"
    mock_settings.GEMINI_API_KEY = None
    mock_settings.GEMINI_MODEL = "gemini-1.5-pro"
    mock_settings.MAX_LLM_RETRIES = 1
    
    svc = LLMService()
    
    # Missing GEMINI_API_KEY triggers ValueError cleanly
    with pytest.raises(ValueError, match="GEMINI_API_KEY not configured"):
        svc.generate_structured("sys", "user", IssueAnalysis)

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_gemini_structured_issue_analysis_returned(mock_openai, mock_settings):
    mock_settings.LLM_PROVIDER = "gemini"
    mock_settings.GEMINI_API_KEY = "valid"
    mock_settings.GEMINI_MODEL = "gemini-1.5-pro"
    mock_settings.MAX_LLM_RETRIES = 1
    
    # Mocking OpenAI wrapper's parse natively
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
    
    # Ensures OpenAI was instantiated using beta Gemini proxy WITHOUT openai key
    mock_openai.assert_called_once_with(api_key="valid", base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    
    assert result.problem_summary == "sum"
    assert "file.py" in result.affected_areas

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_gemini_invalid_structured_output_rejected(mock_openai, mock_settings):
    mock_settings.LLM_PROVIDER = "gemini"
    mock_settings.GEMINI_API_KEY = "valid"
    mock_settings.GEMINI_MODEL = "gemini-1.5-pro"
    mock_settings.MAX_LLM_RETRIES = 0
    
    mock_client = MagicMock()
    mock_openai.return_value = mock_client
    
    # Mock unstructured invalid JSON missing parse payload natively triggering error
    mock_client.beta.chat.completions.parse.side_effect = Exception("ValidationError: missing fields")
    
    svc = LLMService()
    with pytest.raises(RuntimeError, match="maximum retry bounds exceeded"):
        svc.generate_structured("sys", "user", IssueAnalysis)

@patch("app.services.llm_service.settings")
@patch("app.services.llm_service.openai.OpenAI")
def test_ollama_provider_selection(mock_openai, mock_settings):
    mock_settings.LLM_PROVIDER = "ollama"
    mock_settings.OLLAMA_MODEL = "qwen2.5-coder:7b"
    mock_settings.OLLAMA_BASE_URL = "http://localhost:11434/v1"
    mock_settings.MAX_LLM_RETRIES = 1
    
    mock_client = MagicMock()
    mock_openai.return_value = mock_client
    
    mock_parsed_msg = MagicMock()
    mock_parsed_msg.message.parsed = IssueAnalysis(
        problem_summary="Local LLM summary",
        expected_behavior="Expected",
        current_behavior="Current",
        affected_areas=["main.py"],
        constraints=[]
    )
    mock_client.beta.chat.completions.parse.return_value.choices = [mock_parsed_msg]
    
    svc = LLMService()
    result = svc.generate_structured("sys", "user", IssueAnalysis)
    
    mock_openai.assert_called_once_with(api_key="ollama", base_url="http://localhost:11434/v1")
    assert result.problem_summary == "Local LLM summary"
    assert svc.model == "qwen2.5-coder:7b"
