from fastapi import APIRouter
from sqlalchemy import text
from app.config import settings

router = APIRouter()

@router.get("/health")
def health_check():
    return {"status": "ok"}

@router.get("/health/dependencies")
def dependency_check():
    """Report which external services the pipeline can reach (no secrets in the output)."""
    checks = {}

    try:
        from app.db.session import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as e:
        checks["database"] = {"ok": False, "error": type(e).__name__}

    try:
        from app.services.queue_service import redis_client
        redis_client.ping()
        checks["redis"] = {"ok": True}
    except Exception as e:
        checks["redis"] = {"ok": False, "error": type(e).__name__}

    provider = str(settings.LLM_PROVIDER).lower()
    if provider == "ollama":
        from app.services.llm_service import verify_ollama_available
        result = verify_ollama_available()
        checks["llm"] = {"ok": result["available"], "provider": provider, "model": settings.OLLAMA_MODEL,
                         "message": result["message"]}
    else:
        key = settings.GEMINI_API_KEY if provider == "gemini" else settings.OPENAI_API_KEY
        checks["llm"] = {"ok": bool(key), "provider": provider,
                         "message": "API key configured" if key else f"{provider.upper()}_API_KEY is not set"}

    try:
        import docker
        client = docker.from_env()
        client.ping()
        try:
            client.images.get(settings.SANDBOX_IMAGE)
            checks["docker"] = {"ok": True, "sandbox_image": settings.SANDBOX_IMAGE}
        except Exception:
            checks["docker"] = {"ok": False, "sandbox_image": settings.SANDBOX_IMAGE,
                                "message": "Sandbox image not found; build it from sandbox/Dockerfile"}
    except Exception as e:
        checks["docker"] = {"ok": False, "error": type(e).__name__, "message": "Docker engine not reachable"}

    checks["github_app"] = {"ok": bool(settings.GITHUB_APP_ID and settings.GITHUB_APP_PRIVATE_KEY)}
    checks["webhook_secret"] = {"ok": bool(settings.GITHUB_WEBHOOK_SECRET)}
    return {"status": "ok" if all(c["ok"] for c in checks.values()) else "degraded", "checks": checks,
            "code_mutation_enabled": settings.ENABLE_CODE_MUTATION, "pr_creation_enabled": settings.ENABLE_PR_CREATION}
