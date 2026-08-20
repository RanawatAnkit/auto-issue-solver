import os
import hashlib
import logging
from app.config import settings
from app.agents.schemas import CodeChangePlan

logger = logging.getLogger(__name__)

class PatchService:
    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(workspace_path)
        self.max_files = getattr(settings, "MAX_CHANGED_FILES", 20)
        self.max_diff_bytes = getattr(settings, "MAX_DIFF_BYTES", 500000)
        self.allow_delete = getattr(settings, "ALLOW_FILE_DELETE", False)

    def _hash_file(self, exact_path: str) -> str:
        if not os.path.exists(exact_path): return ""
        with open(exact_path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()

    def _validate_path(self, rel_path: str) -> str:
        # absolute rejections mapping both Unix and Windows structural traversals natively.
        if rel_path.startswith("/") or rel_path.startswith("\\") or ":" in rel_path:
            raise ValueError(f"Absolute paths rejected: {rel_path}")
        if ".." in rel_path:
            raise ValueError(f"Traversal rejected: {rel_path}")
        if rel_path.startswith(".git") or "/.git" in rel_path or "\\.git" in rel_path:
            raise ValueError(f".git modification rejected: {rel_path}")
            
        exact = os.path.abspath(os.path.join(self.workspace_path, rel_path))
        if not exact.startswith(self.workspace_path):
            raise ValueError("Symlink or escape path rejected")
        return exact

    def apply_plan(self, plan: CodeChangePlan) -> dict:
        if len(plan.changes) > self.max_files:
            raise ValueError(f"Exceeded max files threshold: {len(plan.changes)}")
            
        diffs = []
        metrics = []
        
        for change in plan.changes:
            target_path = self._validate_path(change.file_path)
            orig_hash = self._hash_file(target_path)
            orig_sz = os.path.getsize(target_path) if os.path.exists(target_path) else 0
            
            if change.operation == "delete":
                if not self.allow_delete:
                    raise ValueError(f"Delete operation requested but disabled globally: {change.file_path}")
                if os.path.exists(target_path):
                    os.remove(target_path)
            else:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as fw:
                    fw.write(change.patch)
                    
            new_hash = self._hash_file(target_path)
            new_sz = os.path.getsize(target_path) if os.path.exists(target_path) else 0
            
            metrics.append({
                "path": change.file_path, "orig_hash": orig_hash, "new_hash": new_hash,
                "orig_size": orig_sz, "new_size": new_sz
            })
            
            diffs.append(f"--- a/{change.file_path}\n+++ b/{change.file_path}\n@@ modified @@")
            
        total_diff_str = "\n".join(diffs)
        if len(total_diff_str) > self.max_diff_bytes:
            raise ValueError("Diff capacity boundary limit gracefully exceeded.")
            
        return {"summary": total_diff_str, "metrics": metrics}
