import os
import tempfile
from app.services.repository_scanner import RepositoryScanner

def test_repository_scanner_ignores_git():
    scanner = RepositoryScanner()
    with tempfile.TemporaryDirectory() as td:
        git_dir = os.path.join(td, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "config"), "w") as f:
            f.write("ignore me")
            
        src_file = os.path.join(td, "main.py")
        with open(src_file, "w") as f:
            f.write("print('hello')")
            
        files = scanner.scan(td)
        assert len(files) == 1
        assert "main.py" in files[0]

def test_repository_scanner_file_size_limit():
    scanner = RepositoryScanner()
    scanner.max_size_bytes = 10
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "big.py")
        with open(src, "w") as f:
            f.write("A" * 20)
        assert len(scanner.scan(td)) == 0
