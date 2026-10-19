"""Test-runner behaviour that does not need Docker (Docker client mocked)."""
import os
import tempfile
import pytest
from unittest.mock import MagicMock
from app.sandbox.test_runner import (
    TestRunner, classify_pytest_result, PASSED, TESTS_FAILED, NO_TESTS, COLLECTION_ERROR, DEPENDENCY_ERROR,
    ENVIRONMENT_ERROR,
)


@pytest.mark.parametrize("code,output,expected", [
    (0, "3 passed", PASSED),
    (1, "1 failed, 2 passed", TESTS_FAILED),
    (5, "collected 0 items\nno tests ran", NO_TESTS),
    (2, "ERROR collecting tests/test_a.py\nSyntaxError", COLLECTION_ERROR),
    (2, "E   ModuleNotFoundError: No module named 'fastapi'", DEPENDENCY_ERROR),
    (2, "E   ModuleNotFoundError: No module named 'app'", COLLECTION_ERROR),  # local package: a code/config problem
    (127, "pytest: not found", ENVIRONMENT_ERROR),
])
def test_classify_pytest_result(code, output, expected):
    assert classify_pytest_result(code, output, ["app/__init__.py", "app/main.py"]) == expected


def _container(code=0, out=b"ok", err=b""):
    c = MagicMock()
    c.wait.return_value = {"StatusCode": code}
    c.logs.side_effect = lambda stdout=True, stderr=True: out if stdout else err
    return c


def _repo(files):
    td = tempfile.mkdtemp()
    for rel, content in files.items():
        path = os.path.join(td, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return td


def test_no_hardcoded_test_target():
    # Regression: tests/test_tasks.py (one specific repository's file) was always appended.
    td = _repo({"tests/test_tasks.py": "def test_x(): pass\n", "tests/test_other.py": "def test_y(): pass\n"})
    runner = TestRunner(td)
    runner.dock = MagicMock()
    runner.dock.containers.run.return_value = _container(0, b"2 passed")
    res = runner.run_tests("python")
    cmd = runner.dock.containers.run.call_args.kwargs["command"]
    assert "tests/test_tasks.py" not in cmd
    assert res["outcome"] == PASSED and res["summary"] == {"passed": 2}


def test_dependencies_from_requirements_and_pyproject():
    td = _repo({
        "requirements.txt": "fastapi>=0.110\\nhttpx\\n",  # literal \n separators seen in the wild
        "pyproject.toml": '[project]\nname = "x"\ndependencies = ["pydantic>=2"]\n[project.optional-dependencies]\ntest = ["pytest-asyncio"]\n',
    })
    reqs = TestRunner(td)._python_requirements().splitlines()
    assert reqs[:2] == ["fastapi>=0.110", "httpx"]
    assert "pydantic>=2" in reqs and "pytest-asyncio" in reqs and "pytest" in reqs


def test_unsupported_repository_is_environment_error_not_code_failure():
    td = _repo({"main.go": "package main\n"})
    runner = TestRunner(td)
    runner.dock = MagicMock()
    res = runner.run_tests()
    assert res["success"] is False and res["outcome"] == ENVIRONMENT_ERROR
    runner.dock.containers.run.assert_not_called()


def test_docker_unavailable_is_reported_not_raised():
    runner = TestRunner(_repo({"test_a.py": "def test_a(): pass\n"}))
    runner.dock = None
    res = runner.run_tests()
    assert res["outcome"] == ENVIRONMENT_ERROR and "Docker" in res["stderr"]


def test_containers_are_hardened_and_cleaned_up_on_start_failure():
    runner = TestRunner(_repo({"test_a.py": "def test_a(): pass\n"}))
    runner.dock = MagicMock()
    leftover = MagicMock()
    runner.dock.containers.list.return_value = [leftover]
    runner.dock.containers.run.side_effect = RuntimeError("cannot start container")
    res = runner.verify_dependencies("python")
    assert res["valid"] is False
    leftover.remove.assert_called()  # no orphaned "Created" containers
    kwargs = runner.dock.containers.run.call_args.kwargs
    assert kwargs["network_mode"] == "none" and kwargs["cap_drop"] == ["ALL"]
    assert kwargs["labels"] == {"auto-issue-solver": "sandbox"}


def test_stdout_and_stderr_are_kept_separately():
    runner = TestRunner(_repo({"test_a.py": "def test_a(): pass\n"}))
    runner.dock = MagicMock()
    runner.dock.containers.run.side_effect = [_container(0, b"pytest 8"), _container(1, b"1 failed", b"warning on stderr")]
    res = runner.run_tests("python")
    assert res["stdout"] == "1 failed" and res["stderr"] == "warning on stderr"
    assert res["outcome"] == TESTS_FAILED
