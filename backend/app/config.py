import os
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    # Plan size limits; larger plans are sent back to the planner to be narrowed.
    PLAN_MAX_FILES: int = 4
    PLAN_MAX_SYMBOLS_PER_FILE: int = 4
    MAX_DIFF_BYTES: int = 500000
    ALLOW_FILE_DELETE: bool = False

    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 100
    LLM_PROVIDER: str = "ollama"
    OPENAI_MODEL: str = 'gpt-4o'
    # Point the "openai" provider at any OpenAI-compatible API, e.g. https://api.groq.com/openai/v1
    OPENAI_BASE_URL: str | None = None
    MAX_LLM_RETRIES: int = 2
    # A 429 whose retry delay is at most this many seconds (per-minute limits) is
    # waited out; longer delays (daily quotas) fail immediately.
    LLM_RATE_LIMIT_MAX_WAIT_SECONDS: int = 90
    MAX_CONTEXT_CHUNKS: int = 10
    MAX_CONTEXT_TOKENS: int = 12000
    MAX_FILE_SIZE_MB: int = 1
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    OPENAI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.8-flash"
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
    # Reasoning effort for thinking models: "low"/"medium"/"high" (gpt-oss) or "true"/"false".
    # Empty: not sent (models without a reasoning mode reject the parameter).
    OLLAMA_THINK: str = ""

    # SWE-bench harness (python -m app.eval). Relative paths are resolved against backend/.
    EVAL_CACHE_DIR: str = os.path.join(_BACKEND_DIR, ".eval_cache")
    EVAL_RESULTS_DIR: str = os.path.join(_BACKEND_DIR, "eval_results")
    EVAL_TEST_TIMEOUT_SECONDS: int = 1800
    EVAL_MEMORY_LIMIT: str = "2g"
    # Repair attempts when the agent's own tests fail on its patch (0 disables repair).
    EVAL_REPAIR_ATTEMPTS: int = 2

    model_config = SettingsConfigDict(
        env_file=os.path.join(_BACKEND_DIR, "..", ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @model_validator(mode="after")
    def _resolve_eval_dirs(self):
        for name in ("EVAL_CACHE_DIR", "EVAL_RESULTS_DIR"):
            path = getattr(self, name)
            if not os.path.isabs(path):
                setattr(self, name, os.path.normpath(os.path.join(_BACKEND_DIR, path)))
        return self

    @model_validator(mode="after")
    def _derive_database_url(self):
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        return self

settings = Settings()
