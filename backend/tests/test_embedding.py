import pytest
from unittest.mock import patch, MagicMock
from app.services.embedding_service import EmbeddingService
from app.config import Settings

@patch("app.services.embedding_service.settings")
@patch("sentence_transformers.SentenceTransformer")
def test_local_provider_selection(mock_st, mock_settings):
    mock_settings.EMBEDDING_PROVIDER = "local"
    mock_settings.EMBEDDING_MODEL = "test-model"
    mock_settings.OPENAI_API_KEY = None # No key required
    
    svc = EmbeddingService()
    assert svc.provider == "local"
    assert svc.model == "test-model"
    mock_st.assert_called_once_with("test-model")

@patch("app.services.embedding_service.settings")
@patch("sentence_transformers.SentenceTransformer")
def test_local_returned_dimension_is_384(mock_st, mock_settings):
    mock_settings.EMBEDDING_PROVIDER = "local"
    mock_settings.OPENAI_API_KEY = None
    
    mock_model = MagicMock()
    mock_st.return_value = mock_model
    
    mock_encoded = MagicMock()
    mock_encoded.tolist.return_value = [[0.1]*384]
    mock_model.encode.return_value = mock_encoded
    
    svc = EmbeddingService()
    res = svc.generate_embeddings(["test"])
    assert len(res) == 1
    assert len(res[0]) == 384

@patch("app.services.embedding_service.settings")
@patch("sentence_transformers.SentenceTransformer")
def test_local_invalid_dimensions_are_rejected(mock_st, mock_settings):
    mock_settings.EMBEDDING_PROVIDER = "local"
    
    mock_model = MagicMock()
    mock_st.return_value = mock_model
    
    mock_encoded = MagicMock()
    mock_encoded.tolist.return_value = [[0.1]*128] # Invalid dimension
    mock_model.encode.return_value = mock_encoded
    
    svc = EmbeddingService()
    with pytest.raises(ValueError, match="CRITICAL DIMENSIONALITY MISMATCH"):
        svc.generate_embeddings(["fail"])
