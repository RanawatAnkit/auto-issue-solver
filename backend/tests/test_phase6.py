import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock
from app.agents.schemas import CodeChangePlan, CodeChange
from app.services.patch_service import PatchService
from app.sandbox.test_runner import TestRunner

def test_coding_agent_schema(monkeypatch):
    monkeypatch.setattr("app.config.settings.LLM_PROVIDER", "openai")
    monkeypatch.setattr("app.config.settings.OPENAI_API_KEY", "mock")
    from app.agents.coding_agent import CodingAgent
    agent = CodingAgent()
    with patch("app.services.llm_service.openai.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_msg = MagicMock()
        
        # Test schema constraints
        dummy_plan = CodeChangePlan(changes=[CodeChange(file_path="t.py", operation="modify", description="x", patch="y")])
        mock_msg.message.parsed = dummy_plan
        mock_client.beta.chat.completions.parse.return_value.choices = [mock_msg]
        
        res = agent.generate_changes({}, {}, "")
        assert res.changes[0].file_path == "t.py"

def test_patch_service_traversal_rejections():
    with tempfile.TemporaryDirectory() as td:
        ps = PatchService(td)
        
        w_args = [
            ("../t.py", "Traversal rejected"),
            ("/etc/passwd", "Absolute paths"),
            ("C:\\Windows\\System32", "Absolute paths"),
            (".git/config", ".git modification"),
        ]
        for path, err in w_args:
            plan = CodeChangePlan(changes=[CodeChange(file_path=path, operation="modify", description="", patch="")])
            with pytest.raises(ValueError, match=err):
                ps.apply_plan(plan)

def test_patch_service_success():
    with tempfile.TemporaryDirectory() as td:
        ps = PatchService(td)
        plan = CodeChangePlan(changes=[CodeChange(file_path="src/main.py", operation="create", description="", patch="print(1)")])
        
        metrics = ps.apply_plan(plan)
        assert os.path.exists(os.path.join(td, "src/main.py"))
        assert metrics["metrics"][0]["orig_size"] == 0
        assert metrics["metrics"][0]["new_size"] > 0
        
def test_docker_sandbox_configurations():
    with tempfile.TemporaryDirectory() as td:
        tr = TestRunner(td)
        tr.dock = MagicMock()
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"success"
        tr.dock.containers.run.return_value = mock_container
        
        res = tr.run_tests("python")
        
        kwargs = tr.dock.containers.run.call_args[1]
        assert kwargs["network_mode"] == "none"
        assert kwargs["privileged"] is False
        assert "mem_limit" in kwargs
        assert "/var/run/docker.sock" not in str(kwargs["volumes"])
        assert res["success"] is True
