import docker
import os
import logging
from app.config import settings

logger = logging.getLogger(__name__)

class TestRunner:
    def __init__(self, workspace_path: str):
        self.workspace_path = self._validate_workspace(workspace_path)
        try:
            self.dock = docker.from_env()
        except docker.errors.DockerException:
            self.dock = None
            logger.warning("Docker unlinked locally. Mocks strictly required for unit executions.")
            
        self.timeout = getattr(settings, "TEST_TIMEOUT_SECONDS", 120)
        self.max_output = getattr(settings, "MAX_TEST_OUTPUT_BYTES", 100000)

    def _validate_workspace(self, workspace_path: str) -> str:
        abs_path = os.path.abspath(workspace_path)
        current_script_dir = os.path.abspath(os.path.dirname(__file__))
        project_root = os.path.abspath(os.path.join(current_script_dir, "../../"))
        if abs_path == project_root or abs_path == os.path.join(project_root, "backend"):
            raise ValueError(f"Unsafe workspace mapping rejected: {abs_path}")
        return abs_path

    def run_tests(self, language: str = "python") -> dict:
        if not self.dock: raise RuntimeError("Docker engine offline.")
        
        cmd = ["pytest"]
        if language == "node": cmd = ["npm", "test"]
            
        try:
            container = self.dock.containers.run(
                image=getattr(settings, "SANDBOX_IMAGE", "python:3.11-slim"),
                command=cmd,
                volumes={self.workspace_path: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                network_mode="none",
                mem_limit=getattr(settings, "SANDBOX_MEMORY_LIMIT", "512m"),
                nano_cpus=int(getattr(settings, "SANDBOX_CPU_LIMIT", 1) * 1e9),
                privileged=False,
                detach=True,
                auto_remove=False,
            )
            
            try:
                res = container.wait(timeout=self.timeout)
                logs = container.logs().decode('utf-8')
                
                if len(logs) > self.max_output: logs = logs[:self.max_output] + "...(truncated)"
                    
                exit_code = res.get("StatusCode", -1)
                
                return {
                    "success": exit_code == 0,
                    "exit_code": exit_code,
                    "stdout": logs,
                    "stderr": "",
                    "duration_seconds": 0.0,
                    "timed_out": False
                }
            except Exception as e:
                container.kill()
                return {"success": False, "exit_code": 124, "stdout": "", "stderr": f"Timeout/Error: {e}", "timed_out": True, "duration_seconds": 0.0}
            finally:
                container.remove(force=True)
                
        except Exception as err:
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": str(err), "timed_out": False, "duration_seconds": 0.0}
