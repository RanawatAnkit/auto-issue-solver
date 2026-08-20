import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DB_NAME: str = "auto_issue_solver"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DATABASE_URL: str = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    REDIS_URL: str = "redis://localhost:6379/0"
    
    GITHUB_APP_ID: str | None = None
    GITHUB_APP_PRIVATE_KEY: str | None = None
    GITHUB_WEBHOOK_SECRET: str = "your_webhook_secret"
    
    GITHUB_OAUTH_CLIENT_ID: str | None = None
    GITHUB_OAUTH_CLIENT_SECRET: str | None = None
    GITHUB_OAUTH_REDIRECT_URI: str = "http://localhost:8000/auth/github/callback"
    SESSION_SECRET_KEY: str = "super_secret_session_key_replace_in_prod"
    SESSION_EXPIRE_HOURS: int = 24
    
    MAX_REPAIR_ATTEMPTS: int = 3
    SANDBOX_IMAGE: str = "python:3.11-slim"

    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 100
    LLM_PROVIDER: str = 'gemini'
    OPENAI_MODEL: str = 'gpt-4o'
    MAX_LLM_RETRIES: int = 2
    MAX_CONTEXT_CHUNKS: int = 10
    MAX_CONTEXT_TOKENS: int = 12000
    MAX_FILE_SIZE_MB: int = 1
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-1.5-pro"

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
