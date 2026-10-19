import os
import tempfile
import pytest
from app.sandbox.test_runner import TestRunner
from app.sandbox.executor import SandboxExecutor

pytestmark = pytest.mark.docker

def test_real_docker_smoke(monkeypatch):
    import docker
    import io
    client = docker.from_env()
    dockerfile = b"FROM python:3.11-slim\nRUN pip install pytest\n"
    client.images.build(fileobj=io.BytesIO(dockerfile), tag="test-smoke-sandbox:latest", rm=True)
    
    with tempfile.TemporaryDirectory() as td:
        test_file = os.path.join(td, "test_math.py")
        with open(test_file, "w") as f:
            f.write("def test_add():\n    assert 1 + 1 == 2\n")
            
        monkeypatch.setattr("app.config.settings.SANDBOX_IMAGE", "test-smoke-sandbox:latest")
        runner = TestRunner(td)
        res = runner.run_tests("python")
        
        print("\nDOCKER_SMOKE_RESULT:", res)
        assert res["success"] is True
        assert res["exit_code"] == 0
        assert "1 passed" in res["stdout"]

def test_sandbox_executor_safe_command():
    with tempfile.TemporaryDirectory() as td:
        executor = SandboxExecutor()
        exit_code, stdout, stderr = executor.execute_command(td, ["echo", "hello"])
        assert exit_code == 0
        assert "hello" in stdout
        
def test_sandbox_executor_rejects_string():
    executor = SandboxExecutor()
    with pytest.raises(ValueError, match="structured list"):
        executor.execute_command("/tmp", "pytest")

def test_sandbox_workspace_validation():
    # Use real project root string dynamically
    bad_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
    executor = SandboxExecutor()
    with pytest.raises(ValueError, match="Unsafe workspace"):
        executor.execute_command(bad_path, ["echo", "hello"])

    with pytest.raises(ValueError, match='Unsafe workspace'):
        TestRunner(bad_path)
    # The instantiation itself should fail

def test_runner_dependency_validation_success(monkeypatch):
    """Verify that TestRunner succeeds pre-flight verification on an image with pytest."""
    monkeypatch.setattr("app.config.settings.SANDBOX_IMAGE", "test-sandbox:latest")
    with tempfile.TemporaryDirectory() as td:
        runner = TestRunner(td)
        verification = runner.verify_dependencies("python")
        assert verification["valid"] is True
        assert "pytest" in verification["version"].lower()
        assert "image" in verification

def test_runner_dependency_validation_missing_pytest(monkeypatch):
    """
    Regression test: TestRunner must identify missing pytest before starting
    the container, capturing image, python executable, installed packages, and error details.
    """
    monkeypatch.setattr("app.config.settings.SANDBOX_IMAGE", "python:3.11-slim")
    with tempfile.TemporaryDirectory() as td:
        runner = TestRunner(td)
        verification = runner.verify_dependencies("python")
        assert verification["valid"] is False
        assert verification["image"] == "python:3.11-slim"
        assert "python" in verification["python_executable"].lower()
        assert "installed_packages" in verification
        assert "pytest" not in verification["installed_packages"].lower()

        # run_tests must fail-fast with exit code 127 without claiming success
        result = runner.run_tests("python")
        assert result["success"] is False
        assert result["exit_code"] == 127
        assert "Pre-flight dependency validation failed" in result["stderr"]
        assert "python:3.11-slim" in result["stderr"]

