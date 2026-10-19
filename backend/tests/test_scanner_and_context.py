import os
import subprocess
import tempfile
import pytest
from unittest.mock import MagicMock
from app.services.repository_scanner import RepositoryScanner, list_tracked_files
from app.agents.context_builder import ContextBuilder
from app.agents.schemas import IssueAnalysis


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as td:
        files = {
            "app/tasks.py": b"def overdue_tasks(tasks):\n    return []\n",
            "app/users.py": b"def get_user():\n    pass\n",
            "pyproject.toml": b"[project]\nname = 'x'\n",
            "package-lock.json": b"{}",
            "logo.png": b"\x89PNG\x00\x00binary",
        }
        for rel, content in files.items():
            os.makedirs(os.path.join(td, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(td, rel), "wb") as f:
                f.write(content)
        subprocess.run(["git", "init", "-q"], cwd=td, check=True)
        subprocess.run(["git", "add", "-A"], cwd=td, check=True)
        with open(os.path.join(td, "untracked_notes.py"), "w") as f:
            f.write("x = 1\n")
        yield td


def test_tracked_files_come_from_git(repo):
    tracked = list_tracked_files(repo)
    assert "pyproject.toml" in tracked and "logo.png" in tracked
    assert "untracked_notes.py" not in tracked


def test_scan_skips_binaries_lockfiles_and_untracked(repo):
    scanned = RepositoryScanner().scan(repo)
    assert sorted(scanned) == ["app/tasks.py", "app/users.py", "pyproject.toml"]


def test_lexical_retrieval_works_without_embeddings(repo):
    builder = ContextBuilder(MagicMock())
    builder.retriever = None  # semantic retrieval unavailable
    analysis = IssueAnalysis(problem_summary="Add overdue filtering for tasks", expected_behavior="overdue tasks listed",
                             current_behavior="no overdue filter", affected_areas=["app/tasks.py"])
    ctx = builder.build_context(1, analysis, repo, list_tracked_files(repo))
    assert ctx[0].file_path == "app/tasks.py"
    assert "def overdue_tasks" in ctx[0].content


def test_semantic_retrieval_failure_degrades_gracefully(repo):
    builder = ContextBuilder(MagicMock())
    builder.retriever = MagicMock()
    builder.retriever.search.side_effect = RuntimeError("operator does not exist: vector <=> vector")
    analysis = IssueAnalysis(problem_summary="overdue tasks", expected_behavior="e", current_behavior="c")
    ctx = builder.build_context(1, analysis, repo, list_tracked_files(repo))
    assert any(c.file_path == "app/tasks.py" for c in ctx)
