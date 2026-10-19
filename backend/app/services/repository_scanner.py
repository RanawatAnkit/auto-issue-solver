import os
import logging
import subprocess
from app.config import settings
from typing import List

logger = logging.getLogger(__name__)

IGNORE_DIRS = {
    ".git", "node_modules", "venv", ".venv", "env", "build", "dist", "coverage", "__pycache__",
    "target", "vendor", ".tox", ".mypy_cache", ".pytest_cache", ".next", ".nuxt", "out", "site-packages",
}
IGNORE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "Cargo.lock",
    "composer.lock", "Gemfile.lock", "go.sum",
}


def _is_ignored(rel_path: str) -> bool:
    parts = rel_path.split("/")
    if any(p in IGNORE_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    return name in IGNORE_FILES or name.endswith((".min.js", ".min.css", ".map"))


def _is_text_file(full_path: str) -> bool:
    try:
        with open(full_path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # A multi-byte character may be cut off at the end of the sample.
        if e.start < len(head) - 4:
            return False
    return True


def list_tracked_files(workspace_path: str) -> List[str]:
    """All files tracked by git in the checkout (repo-relative, forward slashes).

    This is the authoritative list for validating plan/patch paths: unlike the
    indexing scan it is not limited to particular extensions, so files such as
    pyproject.toml, Dockerfile or setup.cfg can be modified.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=workspace_path, capture_output=True, timeout=60, check=True
        ).stdout.decode("utf-8", errors="replace")
        files = [p for p in out.split("\0") if p]
        if files:
            return files
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning(f"git ls-files failed in {workspace_path} ({e}); falling back to a directory walk")

    files = []
    for root, dirs, names in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for name in names:
            files.append(os.path.relpath(os.path.join(root, name), workspace_path).replace("\\", "/"))
    return sorted(files)


class RepositoryScanner:
    def __init__(self):
        self.ignore_dirs = IGNORE_DIRS
        self.supported_extensions = {
            ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".scala", ".cpp", ".cc",
            ".c", ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".html", ".css", ".scss",
            ".vue", ".svelte", ".sql", ".sh", ".md", ".rst", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
        }
        self.max_size_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024

    def scan(self, workspace_path: str) -> List[str]:
        """Text source files worth indexing for retrieval."""
        source_files = []
        for rel_path in list_tracked_files(workspace_path):
            if _is_ignored(rel_path):
                continue
            if os.path.splitext(rel_path)[1].lower() not in self.supported_extensions:
                continue
            full_path = os.path.join(workspace_path, rel_path)
            # Symlinks could point outside the checkout (e.g. at host files).
            if os.path.islink(full_path) or not os.path.isfile(full_path):
                continue
            if os.path.getsize(full_path) > self.max_size_bytes:
                logger.warning(f"File skipped (too large): {rel_path}")
                continue
            if not _is_text_file(full_path):
                continue
            source_files.append(rel_path)
        return source_files
