import logging
import openai
from pydantic import BaseModel
from typing import Type, TypeVar, Optional
from app.config import settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

class LLMService:
    def __init__(self):
        self.provider = getattr(settings, "LLM_PROVIDER", "openai").lower()
        self.max_retries = getattr(settings, "MAX_LLM_RETRIES", 2)
        
        if self.provider == "gemini":
            self.api_key = settings.GEMINI_API_KEY
            self.model = getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
            self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        elif self.provider == "openai":
            self.api_key = settings.OPENAI_API_KEY
            self.model = getattr(settings, "OPENAI_MODEL", "gpt-4o")
            self.base_url = None
        elif self.provider in ("ollama", "local"):
            self.api_key = "ollama"
            self.model = getattr(settings, "OLLAMA_MODEL", "qwen2.5-coder:7b")
            self.base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434/v1")
        else:
            raise NotImplementedError(f"LLM Provider {self.provider} not supported.")
    
    def generate_structured(self, system_prompt: str, user_prompt: str, response_model: Type[T]) -> Optional[T]:
        if not self.api_key:
            raise ValueError(f"Configuration Error: {self.provider.upper()}_API_KEY not configured. Cannot process real LLM requests.")

        if self.base_url:
            client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            client = openai.OpenAI(api_key=self.api_key)
        
        attempt = 0
        while attempt <= self.max_retries:
            try:
                response = client.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format=response_model,
                )
                if response.choices[0].message.parsed:
                    return response.choices[0].message.parsed
                raise ValueError("Parsed structured output returned Null.")
            except Exception as e:
                logger.error(f"LLM Generation failure (attempt {attempt}): {str(e)}")
                attempt += 1
                
        raise RuntimeError("LLM maximum retry bounds exceeded without structured evaluation.")
