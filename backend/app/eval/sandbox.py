"""Docker images and test execution for benchmark instances.

Two layers, mirroring the official harness:
  * an environment image per (repo, version): Python + pinned packages, cached;
  * a throwaway instance image per evaluation: the patched source tree copied in
    and installed. Installation needs the network (pip); the tests then run in a
    container that has no network unless the task requires it and it was allowed.
"""
import hashlib
import io
import logging
import os
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass

import docker

from app.config import settings
from app.eval.environments import EnvSpec
from app.sandbox.test_runner import SANDBOX_LABEL, sandbox_container_kwargs

logger = logging.getLogger(__name__)

_BUILD_TIMEOUT = 1800


class EnvironmentFailure(RuntimeError):
    """The test environment could not be built or started; says nothing about the patch."""


@dataclass
class TestRun:
    __test__ = False

    log: str
    exit_code: int
    timed_out: bool
    duration_seconds: float


def _add_bytes(tar: tarfile.TarFile, name: str, data: str) -> None:
    encoded = data.encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(encoded)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(encoded))


def _build_log(err: docker.errors.BuildError) -> str:
    return "".join(chunk.get("stream", "") or chunk.get("error", "") for chunk in err.build_log
                   if isinstance(chunk, dict))[-4000:]


class EvalSandbox:
    def __init__(self, client=None):
        try:
            self.dock = client or docker.from_env()
            self.dock.ping()
        except Exception as e:
            raise EnvironmentFailure(f"Docker engine is not reachable ({type(e).__name__}); start Docker and retry.") from e

    @staticmethod
    def env_image_tag(repo: str, version: str, spec: EnvSpec) -> str:
        digest = hashlib.sha256(repr((spec.python, spec.pip_packages)).encode()).hexdigest()[:10]
        return f"ais-swe-env:{repo.replace('/', '__').lower()}-{version}-{digest}"

    def ensure_env_image(self, repo: str, version: str, spec: EnvSpec) -> str:
        tag = self.env_image_tag(repo, version, spec)
        try:
            self.dock.images.get(tag)
            return tag
        except docker.errors.ImageNotFound:
            pass
        lines = [
            f"FROM python:{spec.python}-slim",
            "ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1",
        ]
        if spec.pip_packages:
            lines.append("RUN python -m pip install " + " ".join(f'"{p}"' for p in spec.pip_packages))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            _add_bytes(tar, "Dockerfile", "\n".join(lines) + "\n")
        buf.seek(0)
        logger.info("Building evaluation environment image %s", tag)
        try:
            self.dock.images.build(fileobj=buf, custom_context=True, tag=tag, rm=True, forcerm=True,
                                   labels=SANDBOX_LABEL, timeout=_BUILD_TIMEOUT)
        except docker.errors.BuildError as e:
            raise EnvironmentFailure(f"Building environment image {tag} failed:\n{_build_log(e)}") from e
        except docker.errors.APIError as e:
            raise EnvironmentFailure(f"Docker refused to build {tag}: {e}") from e
        return tag

    def _instance_context(self, workspace: str, env_tag: str, spec: EnvSpec):
        lines = [f"FROM {env_tag}", "WORKDIR /testbed"]
        lines += [f'ENV {k}="{v}"' for k, v in spec.extra_env]
        lines += ["COPY . /testbed", f"RUN {spec.install}"]
        ctx = tempfile.TemporaryFile()
        with tarfile.open(fileobj=ctx, mode="w") as tar:
            _add_bytes(tar, "Dockerfile", "\n".join(lines) + "\n")
            for name in os.listdir(workspace):
                # Git metadata is not needed in the image (and is the bulk of the context).
                if name not in (".git", "Dockerfile"):
                    tar.add(os.path.join(workspace, name), arcname=name)
        ctx.seek(0)
        return ctx

    def run_tests(self, workspace: str, env_tag: str, spec: EnvSpec, test_files: list[str],
                  network: bool, timeout: int) -> TestRun:
        """Install the working copy into a fresh image and run the given test files."""
        tag = f"ais-swe-instance:{uuid.uuid4().hex[:12]}"
        ctx = self._instance_context(workspace, env_tag, spec)
        try:
            try:
                self.dock.images.build(fileobj=ctx, custom_context=True, tag=tag, rm=True, forcerm=True,
                                       labels=SANDBOX_LABEL, timeout=_BUILD_TIMEOUT)
            except docker.errors.BuildError as e:
                raise EnvironmentFailure(f"Installing the repository failed:\n{_build_log(e)}") from e
            except docker.errors.APIError as e:
                raise EnvironmentFailure(f"Docker refused to build the instance image: {e}") from e
            finally:
                ctx.close()
            return self._run(tag, list(spec.test_cmd) + test_files, dict(spec.extra_env), network, timeout)
        finally:
            try:
                self.dock.images.remove(tag, force=True)
            except Exception as e:
                logger.debug("Could not remove instance image %s: %s", tag, e)

    def _run(self, image: str, command: list[str], env: dict, network: bool, timeout: int) -> TestRun:
        kwargs = sandbox_container_kwargs(image, command, network=network)
        kwargs.update(working_dir="/testbed", environment={**env, "PYTHONDONTWRITEBYTECODE": "1"},
                      mem_limit=getattr(settings, "EVAL_MEMORY_LIMIT", "2g"))
        started = time.monotonic()
        try:
            container = self.dock.containers.run(**kwargs)
        except docker.errors.APIError as e:
            raise EnvironmentFailure(f"Could not start the test container: {e}") from e
        try:
            timed_out = False
            try:
                exit_code = container.wait(timeout=timeout).get("StatusCode", -1)
            except Exception:
                timed_out, exit_code = True, 124
                try:
                    container.kill()
                except Exception:
                    pass
            log = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass
        return TestRun(log=log, exit_code=exit_code, timed_out=timed_out,
                       duration_seconds=round(time.monotonic() - started, 1))
