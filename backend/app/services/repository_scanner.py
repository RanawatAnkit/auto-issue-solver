import os
import logging
from app.config import settings
from typing import List

logger = logging.getLogger(__name__)

class RepositoryScanner:
    def __init__(self):
        self.ignore_dirs = {".git", "node_modules", "venv", ".venv", "build", "dist", "coverage", "__pycache__"}
        self.supported_extensions = {
            ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cpp", ".c", ".h", 
            ".hpp", ".go", ".rs", ".rb", ".php", ".html", ".css", ".scss", 
            ".sql", ".md", ".json", ".yaml", ".yml"
        }
        self.max_size_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024

    def scan(self, workspace_path: str) -> List[str]:
        source_files = []
        for root, dirs, files in os.walk(workspace_path):
            # Prune ignored directories directly preventing traversal bugs
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs and not d.startswith('.')]
            
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext not in self.supported_extensions:
                    continue
                    
                full_path = os.path.join(root, file)
                if os.path.getsize(full_path) > self.max_size_bytes:
                    logger.warning(f"File skipped (too large): {full_path}")
                    continue
                
                # Check utf-8 compliance safely against malicious executables
                try:
                    with open(full_path, 'r', encoding='utf-8') as f:
                        f.read(1024)
                except UnicodeDecodeError:
                    continue
                
                rel_path = os.path.relpath(full_path, workspace_path)
                source_files.append(rel_path)
                
        return source_files
