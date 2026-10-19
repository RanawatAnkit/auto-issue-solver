import io
import os
import re
import json
import time
import hashlib
import logging
import tarfile
import tomllib
import docker
from app.config import settings
from app.services.repository_scanner import list_tracked_files

logger = logging.getLogger(__name__)

SANDBOX_LABEL = {"auto-issue-solver": "sandbox"}

# Test outcomes. Only PASSED counts as evidence that a change works. The
# environment outcomes mean the sandbox could not run the tests at all, which
# says nothing about the generated code and must not trigger code "repairs".
PASSED = "PASSED"
TESTS_FAILED = "TESTS_FAILED"
COLLECTION_ERROR = "COLLECTION_ERROR"
NO_TESTS = "NO_TESTS"
DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
TIMEOUT = "TIMEOUT"
ENVIRONMENT_OUTCOMES = {ENVIRONMENT_ERROR, DEPENDENCY_ERROR}

_PY_TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]*|[^/]*_test)\.py$")
_REQUIREMENT_FILES = (
    "requirements.txt", "requirements-dev.txt", "requirements_dev.txt", "dev-requirements.txt",
    "requirements-test.txt", "requirements_test.txt", "test-requirements.txt",
    "requirements/base.txt", "requirements/dev.txt", "requirements/test.txt",
)


def _local_module_names(tracked_files) -> set:
    names = set()
    for p in tracked_files:
        parts = p.split("/")
        if parts[-1].endswith(".py"):
            names.add(parts[0][:-3] if len(parts) == 1 else parts[0])
            if len(parts) > 2 and parts[0] in ("src", "lib"):
                names.add(parts[1])
    return names


def classify_pytest_result(exit_code: int, output: str, tracked_files=()) -> str:
    """Map a pytest exit code and output to an outcome (see module constants)."""
    if exit_code == 0:
        return PASSED
    if exit_code == 5:
        return NO_TESTS
    missing = re.findall(r"ModuleNotFoundError: No module named '([^'.]+)", output)
    local = _local_module_names(tracked_files)
    if missing and any(m not in local for m in missing):
        # A third-party package is not installed in the sandbox.
        return DEPENDENCY_ERROR
    if exit_code == 1:
        return TESTS_FAILED
    if exit_code in (2, 3, 4):
        # 2: interrupted (usually import/collection errors), 3: internal error, 4: usage error
        return COLLECTION_ERROR
    if exit_code in (126, 127):
        return ENVIRONMENT_ERROR
    if exit_code == 137:
        return TIMEOUT if "timed out" in output.lower() else ENVIRONMENT_ERROR  # killed / out of memory
    return TESTS_FAILED


def _summary_counts(output: str) -> dict:
    counts = {}
    for key in ("passed", "failed", "error", "errors", "skipped", "xfailed"):
        m = re.findall(rf"(\d+) {key}\b", output)
        if m:
            counts["errors" if key == "error" else key] = int(m[-1])
    return counts


class TestRunner:
    __test__ = False

    def __init__(self, workspace_path: str):
        self.workspace_path = self._validate_workspace(workspace_path)
        try:
            self.dock = docker.from_env()
        except docker.errors.DockerException:
            self.dock = None
            logger.warning("Docker engine is not reachable; sandboxed test execution is unavailable.")

        self.timeout = getattr(settings, "TEST_TIMEOUT_SECONDS", 300)
        self.max_output = getattr(settings, "MAX_TEST_OUTPUT_BYTES", 100000)

    @property
    def sandbox_image(self) -> str:
        return getattr(settings, "SANDBOX_IMAGE", "test-sandbox:latest")

    def _validate_workspace(self, workspace_path: str) -> str:
        abs_path = os.path.abspath(workspace_path)
        current_script_dir = os.path.abspath(os.path.dirname(__file__))
        project_root = os.path.abspath(os.path.join(current_script_dir, "../../"))
        if abs_path == project_root or abs_path == os.path.join(project_root, "backend"):
            raise ValueError(f"Unsafe workspace mapping rejected: {abs_path}")
        return abs_path

    # ---- test command detection -------------------------------------------------

    def _tracked(self):
        return list_tracked_files(self.workspace_path)

    def detect_language(self) -> str | None:
        tracked = self._tracked()
        names = set(tracked)
        if any(_PY_TEST_FILE_RE.search(p) for p in tracked) or names & {"pytest.ini", "conftest.py", "tox.ini"}:
            return "python"
        if "package.json" in names:
            try:
                with open(os.path.join(self.workspace_path, "package.json"), encoding="utf-8") as f:
                    script = (json.load(f).get("scripts") or {}).get("test", "")
                if script and "no test specified" not in script:
                    return "node"
            except (OSError, ValueError):
                pass
        if any(p.endswith(".py") for p in tracked):
            return "python"
        return None

    def _python_requirements(self) -> str:
        """Collect dependency specifiers from requirement files and pyproject.toml (never executes repo code)."""
        lines = []
        for rel in _REQUIREMENT_FILES:
            path = os.path.join(self.workspace_path, rel)
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            # Some repositories commit requirements with literal "\n" separators.
            if "\\n" in raw and "\n" not in raw.strip():
                raw = raw.replace("\\n", "\n")
            for line in raw.splitlines():
                line = line.split("#", 1)[0].strip()
                # Nested requirement files and editable/local installs would reference
                # paths outside the build context; skip them.
                if line and not line.startswith(("-r", "-c", "-e", "--", ".", "/")):
                    lines.append(line)
        pyproject = os.path.join(self.workspace_path, "pyproject.toml")
        if os.path.isfile(pyproject):
            try:
                with open(pyproject, "rb") as f:
                    project = tomllib.load(f).get("project", {})
                lines.extend(project.get("dependencies", []))
                optional = project.get("optional-dependencies", {})
                for extra in ("test", "tests", "testing", "dev"):
                    lines.extend(optional.get(extra, []))
            except (OSError, tomllib.TOMLDecodeError) as e:
                logger.warning(f"Could not parse pyproject.toml dependencies: {e}")
        # Match the package "pytest" itself, not plugins such as pytest-asyncio.
        if lines and not any(re.match(r"pytest\s*(?:[<>=!~;\[]|$)", l, re.I) for l in lines):
            lines.append("pytest")
        return "\n".join(dict.fromkeys(lines))

    def get_or_build_workspace_image(self, language: str = "python") -> str:
        """
        Ensure the sandbox image contains the repository's manifest dependencies.
        Dependencies are installed at image build time (with network access) so the
        tests themselves can run with networking disabled. Images are cached by
        content hash.
        """
        base_image = self.sandbox_image
        if not self.dock or language != "python":
            return base_image

        requirements = self._python_requirements()
        if not requirements:
            return base_image

        content_hash = hashlib.sha256(f"{base_image}\n{requirements}".encode("utf-8")).hexdigest()[:16]
        derived_tag = f"sandbox-repo:{content_hash}"
        try:
            self.dock.images.get(derived_tag)
            return derived_tag
        except docker.errors.ImageNotFound:
            pass

        dockerfile_content = (
            f"FROM {base_image}\n"
            "COPY requirements.txt /tmp/requirements.txt\n"
            "RUN pip install --no-cache-dir -r /tmp/requirements.txt\n"
        )
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for name, data in (("Dockerfile", dockerfile_content), ("requirements.txt", requirements)):
                encoded = data.encode("utf-8")
                info = tarfile.TarInfo(name=name)
                info.size = len(encoded)
                tar.addfile(info, io.BytesIO(encoded))
        tar_stream.seek(0)
        logger.info(f"Building repository sandbox image '{derived_tag}' from dependency manifests...")
        try:
            self.dock.images.build(fileobj=tar_stream, custom_context=True, tag=derived_tag, rm=True, labels=SANDBOX_LABEL)
        except docker.errors.BuildError as build_err:
            build_log = "".join(chunk.get("stream", "") for chunk in build_err.build_log if isinstance(chunk, dict))
            raise RuntimeError(f"Installing repository dependencies failed:\n{build_log[-3000:]}") from build_err
        logger.info(f"Successfully built repository sandbox image '{derived_tag}'")
        return derived_tag

    # ---- container helpers -----------------------------------------------------

    def _run_container(self, image: str, command: list, timeout: int, mount_workspace: bool = False) -> dict:
        kwargs = dict(
            image=image,
            command=command,
            network_mode="none",
            privileged=False,
            detach=True,
            auto_remove=False,
            labels=SANDBOX_LABEL,
            mem_limit=getattr(settings, "SANDBOX_MEMORY_LIMIT", "1g"),
            nano_cpus=int(getattr(settings, "SANDBOX_CPU_LIMIT", 1) * 1e9),
            pids_limit=512,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            environment={"PYTHONPATH": "/workspace", "PYTHONDONTWRITEBYTECODE": "1", "CI": "true"},
        )
        if mount_workspace:
            kwargs.update(volumes={self.workspace_path: {"bind": "/workspace", "mode": "rw"}}, working_dir="/workspace")

        started = time.monotonic()
        try:
            container = self.dock.containers.run(**kwargs)
        except Exception:
            # run() creates the container before starting it; a failed start leaves
            # it behind in "Created" state with no handle to remove it.
            self._remove_orphans()
            raise
        try:
            try:
                res = container.wait(timeout=timeout)
                timed_out = False
                exit_code = res.get("StatusCode", -1)
            except Exception as wait_err:
                logger.warning(f"Sandbox command exceeded {timeout}s ({wait_err}); killing container")
                try:
                    container.kill()
                except Exception:
                    pass
                timed_out, exit_code = True, 124
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass
        if len(stdout) > self.max_output:
            stdout = "...(truncated)...\n" + stdout[-self.max_output:]
        if len(stderr) > self.max_output:
            stderr = "...(truncated)...\n" + stderr[-self.max_output:]
        return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "timed_out": timed_out,
                "duration_seconds": round(time.monotonic() - started, 2)}

    def _remove_orphans(self):
        try:
            for c in self.dock.containers.list(all=True, filters={"label": "auto-issue-solver=sandbox", "status": "created"}):
                c.remove(force=True)
        except Exception as e:
            logger.warning(f"Could not clean up sandbox containers: {e}")

    def verify_dependencies(self, language: str = "python", image_name: str = None) -> dict:
        """
        Verify that the test framework is installed and discoverable in the runner image before executing tests.
        Returns:
            {"valid": True, "image": image_name, "version": "...", "details": "..."} on success.
            {"valid": False, "image": image_name, "python_executable": "...", "installed_packages": "...", "error": "...", "diagnostic_summary": "..."} on failure.
        """
        if not self.dock:
            raise RuntimeError("Docker engine offline.")

        if image_name is None:
            image_name = self.sandbox_image
        if language == "python":
            verify_cmd = ["python", "-m", "pytest", "--version"]
        elif language == "node":
            verify_cmd = ["npm", "--version"]
        else:
            verify_cmd = ["pytest", "--version"]

        try:
            res = self._run_container(image_name, verify_cmd, timeout=60)
            logs = (res["stdout"] + res["stderr"]).strip()
            if res["exit_code"] == 0:
                return {
                    "valid": True,
                    "image": image_name,
                    "version": logs,
                    "details": f"Test framework verified in image '{image_name}': {logs}"
                }
            return self._capture_diagnostics(image_name, error=f"Pre-flight verification failed (exit code {res['exit_code']}): {logs}")
        except Exception as e:
            return self._capture_diagnostics(image_name, error=str(e))

    def _capture_diagnostics(self, image_name: str, error: str) -> dict:
        """Capture image name, python executable, installed packages, and logs when verification fails."""
        python_exec = "unknown"
        installed_packages = "unknown"

        try:
            python_exec = self._run_container(image_name, ["python", "-c", "import sys; print(sys.executable)"], timeout=20)["stdout"].strip()
        except Exception as py_err:
            python_exec = f"Error probing python executable: {py_err}"

        try:
            installed_packages = self._run_container(image_name, ["python", "-m", "pip", "list"], timeout=30)["stdout"].strip()
        except Exception as pip_err:
            installed_packages = f"Error probing pip packages: {pip_err}"

        diagnostic_summary = (
            f"Image: {image_name}\n"
            f"Python Executable: {python_exec}\n"
            f"Installed Packages:\n{installed_packages}\n"
            f"Failure Reason: {error}"
        )
        logger.error(f"Test runner dependency verification failed:\n{diagnostic_summary}")

        return {
            "valid": False,
            "image": image_name,
            "python_executable": python_exec,
            "installed_packages": installed_packages,
            "error": error,
            "diagnostic_summary": diagnostic_summary
        }

    # ---- public API --------------------------------------------------------------

    @staticmethod
    def _env_failure(message: str, verification: dict | None = None, outcome: str = ENVIRONMENT_ERROR) -> dict:
        return {
            "success": False, "outcome": outcome, "exit_code": 127, "stdout": "", "stderr": message,
            "duration_seconds": 0.0, "timed_out": False, "command": None, "summary": {},
            "verification": verification or {"valid": False, "error": message},
        }

    def run_tests(self, language: str | None = None, test_target: str = None) -> dict:
        if not self.dock:
            return self._env_failure("Docker engine is not reachable; cannot run tests in the sandbox. Start Docker and retry.")

        language = language or self.detect_language()
        if language not in ("python", "node"):
            return self._env_failure(
                "No supported test runner detected for this repository (supported: pytest for Python, "
                "npm test for Node.js)."
            )

        try:
            target_image = self.get_or_build_workspace_image(language)
        except Exception as e:
            return self._env_failure(str(e), outcome=DEPENDENCY_ERROR)

        # Verify the image before executing tests
        verification = self.verify_dependencies(language, image_name=target_image)
        if not verification.get("valid", False):
            diag = verification.get("diagnostic_summary", str(verification.get("error", "")))
            return self._env_failure(f"Pre-flight dependency validation failed: missing test framework.\n{diag}", verification)

        if language == "python":
            cmd = ["python", "-m", "pytest", "-q", "-rfE", "--color=no", "-p", "no:cacheprovider"]
            if test_target:
                cmd.append(test_target)
        else:
            cmd = ["npm", "test", "--silent"]

        try:
            res = self._run_container(target_image, cmd, timeout=self.timeout, mount_workspace=True)
        except Exception as err:
            return self._env_failure(f"Sandbox execution failed: {err}", verification)

        output = res["stdout"] + "\n" + res["stderr"]
        if res["timed_out"]:
            outcome = TIMEOUT
        elif language == "python":
            outcome = classify_pytest_result(res["exit_code"], output, self._tracked())
        else:
            outcome = PASSED if res["exit_code"] == 0 else TESTS_FAILED

        return {
            "success": outcome == PASSED,
            "outcome": outcome,
            "exit_code": res["exit_code"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "duration_seconds": res["duration_seconds"],
            "timed_out": res["timed_out"],
            "command": cmd,
            "image": target_image,
            "summary": _summary_counts(output),
            "verification": verification,
        }
