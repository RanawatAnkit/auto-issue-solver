import os
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DB_NAME: str = "auto_issue_solver"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    # Built from the DB_* parts when not set explicitly (see _derive_database_url).
    DATABASE_URL: str | None = None

    REDIS_URL: str = "redis://localhost:6379/0"

    GITHUB_APP_ID: str | None = None
    GITHUB_APP_PRIVATE_KEY: str | None = None
    # No insecure default: webhooks are rejected until a secret is configured.
    GITHUB_WEBHOOK_SECRET: str | None = None

    GITHUB_OAUTH_CLIENT_ID: str | None = None
    GITHUB_OAUTH_CLIENT_SECRET: str | None = None
    GITHUB_OAUTH_REDIRECT_URI: str = "http://localhost:8000/auth/github/callback"
    FRONTEND_URL: str = "http://localhost:5173"
    SESSION_SECRET_KEY: str = "super_secret_session_key_replace_in_prod"
    SESSION_EXPIRE_HOURS: int = 24

    MAX_REPAIR_ATTEMPTS: int = 3
    REPAIR_TIME_BUDGET_SECONDS: int = 3600
    # Runs left in a non-terminal state longer than this are considered abandoned
    # (e.g. the worker crashed) and no longer block new runs for the same issue.
    RUN_STALE_AFTER_MINUTES: int = 180
    SANDBOX_IMAGE: str = "test-sandbox:latest"
    SANDBOX_MEMORY_LIMIT: str = "1g"
    SANDBOX_CPU_LIMIT: float = 1.0
    TEST_TIMEOUT_SECONDS: int = 300
    MAX_TEST_OUTPUT_BYTES: int = 100000
    ENABLE_CODE_MUTATION: bool = False
    # With code mutation enabled, push a branch and open a PR once tests pass.
    # Set to false to stop after local verification (status COMPLETED) without
    # writing anything to GitHub.
    ENABLE_PR_CREATION: bool = True
    GIT_AUTHOR_NAME: str = "auto-issue-solver[bot]"
    GIT_AUTHOR_EMAIL: str = "auto-issue-solver@users.noreply.github.com"
    MAX_CHANGED_FILES: int = 20
    MAX_DIFF_BYTES: int = 500000
    ALLOW_FILE_DELETE: bool = False

    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 100
    LLM_PROVIDER: str = "ollama"
    OPENAI_MODEL: str = 'gpt-4o'
    MAX_LLM_RETRIES: int = 2
    MAX_CONTEXT_CHUNKS: int = 10
    MAX_CONTEXT_TOKENS: int = 12000
    MAX_FILE_SIZE_MB: int = 1
    # Symbol-level localization before planning; on any failure the planner
    # uses the ordinary retrieved context. Rounds are capped at 5 in code.
    LOCALIZATION_ENABLED: bool = True
    LOCALIZATION_MAX_ROUNDS: int = 5
    LOCALIZATION_RESULT_LIMIT: int = 3
    LOCALIZATION_TIME_BUDGET_SECONDS: int = 900
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.8-flash"
    # OpenRouter (LLM_PROVIDER=openrouter): one key for many hosted models. Pick a model that
    # supports structured outputs; ':free' models are rate-limited and may log prompts.
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_MODEL: str | None = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_TIMEOUT: int = 300
    OPENROUTER_MAX_TOKENS: int = 4096
    # Reasoning models spend output tokens thinking; this allowance is added on top of the
    # answer cap and doubled on demand up to the maximum.
    OPENROUTER_REASONING_TOKENS: int = 8192
    OPENROUTER_REASONING_TOKENS_MAX: int = 32768
    OPENROUTER_CONTEXT_TOKENS: int = 32000
    LLM_FALLBACK_PROVIDER: str | None = None
    # Falling back from a local model to a cloud provider can incur charges, so it
    # must be opted into explicitly.
    LLM_ALLOW_PAID_FALLBACK: bool = False
    # "quota": fall back only on quota/rate-limit errors.
    # "unavailable": also fall back when the primary is unreachable or keeps failing.
    LLM_FALLBACK_ON: str = "quota"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5-coder:7b"
    OLLAMA_TIMEOUT: int = 600
    # Ollama's default context window (2048 tokens on many installs) silently
    # truncates long prompts from the front, dropping the system instructions.
    OLLAMA_NUM_CTX: int = 16384
    OLLAMA_NUM_PREDICT: int = 4096

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode="after")
    def _derive_database_url(self):
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        return self

settings = Settings()
