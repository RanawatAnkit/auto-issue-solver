import os
import tempfile

# The test suite must never touch the development database: previous runs
# filled it with hundreds of throwaway repositories. Point DATABASE_URL at an
# isolated database *before* any app module creates its engine.
# - Default: a throwaway SQLite file (no services needed).
# - TEST_DATABASE_URL: e.g. a dedicated Postgres database for integration runs.
_test_db_url = os.environ.get("TEST_DATABASE_URL") or (
    "sqlite:///" + os.path.join(tempfile.gettempdir(), f"auto_issue_solver_test_{os.getpid()}.db").replace("\\", "/")
)
if not _test_db_url.startswith("sqlite") and "test" not in _test_db_url.rsplit("/", 1)[-1]:
    raise RuntimeError(
        "Refusing to run tests against a database whose name does not contain 'test': "
        "point TEST_DATABASE_URL at a dedicated test database."
    )
os.environ["DATABASE_URL"] = _test_db_url
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.db.session import engine
from app.models import Base
from app.main import app

IS_POSTGRES = engine.dialect.name == "postgresql"


def pytest_sessionstart(session):
    if IS_POSTGRES:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)


def _docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    """Skip (with a visible reason) tests whose external service is unavailable."""
    docker_ok = None
    run_ollama = os.environ.get("RUN_OLLAMA_TESTS") == "1"
    for item in items:
        if "postgres" in item.keywords and not IS_POSTGRES:
            item.add_marker(pytest.mark.skip(reason="needs PostgreSQL+pgvector: set TEST_DATABASE_URL"))
        if "docker" in item.keywords:
            if docker_ok is None:
                docker_ok = _docker_available()
            if not docker_ok:
                item.add_marker(pytest.mark.skip(reason="needs a running Docker engine"))
        if "ollama" in item.keywords and not run_ollama:
            item.add_marker(pytest.mark.skip(reason="live Ollama test: set RUN_OLLAMA_TESTS=1"))


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    if engine.dialect.name == "sqlite" and engine.url.database:
        try:
            os.remove(engine.url.database)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _restore_dependency_overrides():
    # Some tests override FastAPI dependencies (e.g. get_db with a mock session);
    # don't let that leak into later tests.
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
