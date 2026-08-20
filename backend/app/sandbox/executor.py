import docker
import os
import logging
from typing import Tuple
from app.config import settings

logger = logging.getLogger(__name__)

class SandboxExecutor:
    def __init__(self):
        self.client = docker.from_env()
        self.image = settings.SANDBOX_IMAGE
        
    def _validate_workspace(self, workspace_path: str):
        abs_path = os.path.abspath(workspace_path)
        current_script_dir = os.path.abspath(os.path.dirname(__file__))
        project_root = os.path.abspath(os.path.join(current_script_dir, "../../"))
        if abs_path == project_root or abs_path == os.path.join(project_root, "backend"):
            raise ValueError(f"Unsafe workspace mapping rejected: {abs_path}")
        return abs_path

    def execute_command(self, workspace_path: str, command: list, timeout: int = 60) -> Tuple[int, str, str]:
        if not isinstance(command, list):
            raise ValueError("Command must be a structured list[str], shell executions are explicitly banned.")
            
        validated_workspace = self._validate_workspace(workspace_path)

        try:
            logger.info(f"Sandbox Exec: {command}")
            container = self.client.containers.run(
                image=self.image,
                command=command,
                volumes={validated_workspace: {'bind': '/workspace', 'mode': 'rw'}},
                working_dir='/workspace',
                detach=True,
                mem_limit=getattr(settings, "SANDBOX_MEMORY_LIMIT", "512m"),
                nano_cpus=int(getattr(settings, "SANDBOX_CPU_LIMIT", 1) * 1e9),
                network_mode='none',
                privileged=False,
                auto_remove=False
            )
            
            try:
                result = container.wait(timeout=timeout)
                exit_code = result.get("StatusCode", 1)
                msgs = container.logs().decode('utf-8')
                return exit_code, msgs, ""
            except Exception as wait_err:
                container.kill()
                return 124, "", f"Timeout/Error: {wait_err}"
            finally:
                container.remove(force=True)
        except Exception as e:
            return 1, "", str(e)
