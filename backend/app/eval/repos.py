"""Local clones of benchmark repositories.

Each repository is cloned once into the cache; every task then gets a cheap
`--shared` clone checked out at its base commit, which can be modified and
thrown away without touching the cache.
"""
import os
import shutil
import subprocess
import tempfile
import logging

from app.services.github_service import _rmtree_onerror

logger = logging.getLogger(__name__)

# Line-ending conversion on Windows would change every file the tests read and
# make diffs/patches disagree with the dataset.
_NO_CRLF = ["-c", "core.autocrlf=false"]


class GitError(RuntimeError):
    pass


def git(cwd: str | None, *args: str, timeout: int = 600, input_text: str | None = None) -> str:
    # Bytes, not text mode: on Windows text-mode stdin turns "\n" into "\r\n",
    # which corrupts every patch piped to `git apply`.
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=timeout,
                            input=input_text.encode("utf-8") if input_text is not None else None)
    stdout = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise GitError(f"git {' '.join(args[:3])} failed: {(stderr or stdout).strip()[-2000:]}")
    return stdout


class RepoCache:
    def __init__(self, cache_dir: str):
        self.cache_dir = os.path.abspath(cache_dir)

    def _mirror_path(self, repo: str) -> str:
        return os.path.join(self.cache_dir, "repos", repo.replace("/", "__"))

    def ensure(self, repo: str, commit: str) -> str:
        """Return a local clone of `repo` that contains `commit`, cloning or fetching as needed."""
        path = self._mirror_path(repo)
        if not os.path.isdir(os.path.join(path, ".git")):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            logger.info("Cloning https://github.com/%s into the evaluation cache (first use only)", repo)
            git(None, "clone", "--quiet", "--no-checkout", *_NO_CRLF, f"https://github.com/{repo}.git", path,
                timeout=1800)
        try:
            git(path, "cat-file", "-e", f"{commit}^{{commit}}")
        except GitError:
            logger.info("Commit %s not in cached %s; fetching", commit[:12], repo)
            git(path, "fetch", "--quiet", "origin", timeout=1800)
            git(path, "cat-file", "-e", f"{commit}^{{commit}}")
        return path

    def checkout(self, repo: str, commit: str) -> str:
        """A fresh, disposable working copy at `commit`. Call `remove_workspace` when done."""
        source = self.ensure(repo, commit)
        workspace = tempfile.mkdtemp(prefix="ais-eval-")
        try:
            git(None, "clone", "--quiet", "--shared", "--no-checkout", *_NO_CRLF, source, workspace)
            git(workspace, "checkout", "--quiet", "--detach", commit)
        except Exception:
            remove_workspace(workspace)
            raise
        return workspace


def remove_workspace(path: str | None) -> None:
    if path and os.path.isdir(path):
        shutil.rmtree(path, onerror=_rmtree_onerror)


def apply_patch(workspace: str, patch: str) -> None:
    """Apply a unified diff, falling back to a 3-way merge; raises GitError if it does not apply."""
    if not patch.endswith("\n"):
        patch += "\n"
    try:
        git(workspace, "apply", "--whitespace=nowarn", "-", input_text=patch)
    except GitError:
        git(workspace, "apply", "--whitespace=nowarn", "--3way", "-", input_text=patch)


def diff_against(workspace: str, commit: str) -> str:
    """Everything that changed in the working copy since `commit`, including new files."""
    git(workspace, "add", "-A")
    return git(workspace, "diff", "--cached", "--binary", commit)
