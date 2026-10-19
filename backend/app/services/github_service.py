from github import Github, Auth, GithubException
from app.config import settings
import subprocess
import tempfile
import logging
import shutil
import stat
import os
import re

logger = logging.getLogger(__name__)

def _load_private_key() -> str:
    """GITHUB_APP_PRIVATE_KEY may be a path to the .pem file or the PEM text itself."""
    if not settings.GITHUB_APP_ID or not settings.GITHUB_APP_PRIVATE_KEY:
        raise ValueError("GitHub App credentials not configured: set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY.")
    value = settings.GITHUB_APP_PRIVATE_KEY.strip()
    if value.startswith("-----BEGIN"):
        # Single-line .env values usually carry escaped newlines.
        return value.replace("\\n", "\n")
    if not os.path.exists(value):
        raise ValueError("GitHub App private key file does not exist (GITHUB_APP_PRIVATE_KEY path).")
    try:
        with open(value, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        raise ValueError("Could not read GitHub App private key file")


class GitHubService:
    def __init__(self, installation_id: int):
        self.installation_id = installation_id
        private_key = _load_private_key()
        self.auth = Auth.AppAuth(
            settings.GITHUB_APP_ID,
            private_key
        ).get_installation_auth(installation_id)
        self.client = Github(auth=self.auth)

    @classmethod
    def get_installation_id_for_repo(cls, repo_full_name: str) -> int:
        import jwt, time, requests
        private_key = _load_private_key()

        encoded_jwt = jwt.encode({
            'iat': int(time.time()) - 60,
            'exp': int(time.time()) + (10 * 60),
            'iss': settings.GITHUB_APP_ID
        }, private_key, algorithm='RS256')
        
        res = requests.get(f"https://api.github.com/repos/{repo_full_name}/installation", headers={
            'Authorization': 'Bearer ' + encoded_jwt,
            'Accept': 'application/vnd.github.v3+json'
        }, timeout=10)
        
        if res.status_code == 200:
            return res.json()['id']
        if res.status_code == 404:
            raise ValueError(
                f"The GitHub App is not installed on {repo_full_name} (or the repository does not exist). "
                "Install the App on that repository and grant it access."
            )
        if res.status_code == 401:
            raise ValueError("GitHub rejected the App credentials: check GITHUB_APP_ID and the private key.")
        raise ValueError(f"GitHub App installation lookup failed for {repo_full_name}. Status: {res.status_code}")

    def get_installation_token(self):
        return self.auth.token

    def get_repository(self, full_name: str):
        return self.client.get_repo(full_name)

    def get_issue(self, repo_full_name: str, issue_number: int):
        return self.get_repository(repo_full_name).get_issue(issue_number)

    def list_issues(self, repo_full_name: str, state="open"):
        return self.get_repository(repo_full_name).get_issues(state=state)

    def get_issue_comments(self, repo_full_name: str, issue_number: int):
        return self.get_issue(repo_full_name, issue_number).get_comments()

    def check_repository_access(self, repo_full_name: str):
        try:
            repo = self.get_repository(repo_full_name)
            # Ensure it resolves and we can read properties implicitly validating Contents!
            _ = repo.name
        except GithubException as e:
            raise ValueError(f"GitHub App installation does not have access to repository {repo_full_name} or Contents permission is missing.")

    def comment_on_issue(self, repo_full_name: str, issue_number: int, body: str):
        issue = self.get_issue(repo_full_name, issue_number)
        return issue.create_comment(body)

    def update_issue(self, repo_full_name: str, issue_number: int, state: str):
        issue = self.get_issue(repo_full_name, issue_number)
        return issue.edit(state=state)

    def get_branch_sha(self, repo_full_name: str, branch: str = "main"):
        repo = self.get_repository(repo_full_name)
        try:
            ref = repo.get_git_ref(f"heads/{branch}")
            return ref.object.sha
        except GithubException as e:
            if getattr(e, "status", None) == 404:
                return None
            raise

    def create_branch(self, repo_full_name: str, branch_name: str, base_branch: str = "main"):
        repo = self.get_repository(repo_full_name)
        try:
            sha = self.get_branch_sha(repo_full_name, base_branch)
            repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=sha)
        except GithubException as e:
            if e.status == 422: # reference already exists
                pass
            else:
                raise

    def create_pull_request(self, repo_full_name: str, head_branch: str, base_branch: str, title: str, body: str):
        repo = self.get_repository(repo_full_name)
        return repo.create_pull(title=title, body=body, head=head_branch, base=base_branch)

    def find_existing_pull_request(self, repo_full_name: str, issue_number: int, branch_name: str):
        repo = self.get_repository(repo_full_name)
        prs = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch_name}")
        # Ensure it genuinely matches
        for pr in prs:
             if str(issue_number) in (pr.title or "") or str(issue_number) in (pr.body or ""):
                 return pr
        return None

# Patterns for credential *values*. Merely mentioning an identifier such as
# `api_key` in code (very common) is not a secret.
SECRET_PATTERNS = [
    r'gh[pousr]_[A-Za-z0-9]{36,}',
    r'github_pat_[A-Za-z0-9_]{50,}',
    r'-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----',
    r'\bAKIA[0-9A-Z]{16}\b',
    r'(?i)aws[_-]?secret[_-]?access[_-]?key\s*[=:]\s*["\']?[A-Za-z0-9/+=]{40}',
    r'\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}',
    r'\bAIza[0-9A-Za-z_\-]{35}\b',
    r'\bxox[baprs]-[A-Za-z0-9-]{10,}',
    # A hard-coded literal assigned to something named like a credential.
    r'(?i)\b[\w-]*(?:api[_-]?key|secret|token|passw(?:or)?d)[\w-]*\s*[=:]\s*["\'][A-Za-z0-9_\-/+=.]{16,}["\']',
]

def find_secrets(files_content: list[str]) -> list[str]:
    """Return the names of the secret patterns found (never the matched values)."""
    hits = []
    for content in files_content:
        for p in SECRET_PATTERNS:
            if re.search(p, content) and p not in hits:
                hits.append(p)
    return hits

def validate_no_secrets(files_content: list[str]) -> bool:
    return not find_secrets(files_content)

def _auth_header_args(auth_token: str) -> list:
    import base64
    b64_auth = base64.b64encode(f"x-access-token:{auth_token}".encode('ascii')).decode('ascii')
    return ["-c", f"http.extraHeader=AUTHORIZATION: Basic {b64_auth}"]

def _rmtree_onerror(func, path, _exc):
    # Git marks object files read-only, which makes deletion fail on Windows.
    os.chmod(path, stat.S_IWRITE)
    func(path)

class GitWorkspace:
    def __init__(self, clone_url: str):
        self.clone_url = clone_url
        self.workspace_path = tempfile.mkdtemp(prefix="ais-workspace-")

    def run_git_command(self, args: list, timeout: int = 60, obscure_token: str = None):
        # ALWAYS strict subprocess parsing to prevent Shell injection.
        cmd = ["git"] + args
        try:
            result = subprocess.run(
                cmd, 
                cwd=self.workspace_path, 
                capture_output=True, 
                text=True, 
                timeout=timeout,
                shell=False
            )
            if result.returncode != 0:
                safe_cmd = " ".join(cmd)
                safe_err = result.stderr
                if obscure_token:
                    safe_cmd = safe_cmd.replace(obscure_token, "***")
                    safe_err = safe_err.replace(obscure_token, "***")
                safe_err = re.sub(r'x-access-token:[^@]+@', 'x-access-token:***@', safe_err)
                safe_err = re.sub(r'gh[sp]_[a-zA-Z0-9]+', '***', safe_err)
                safe_err = re.sub(r'Basic [A-Za-z0-9+/=]+', 'Basic ***', safe_err)
                safe_cmd = re.sub(r'x-access-token:[^@]+@', 'x-access-token:***@', safe_cmd)
                safe_cmd = re.sub(r'gh[sp]_[a-zA-Z0-9]+', '***', safe_cmd)
                safe_cmd = re.sub(r'Basic [A-Za-z0-9+/=]+', 'Basic ***', safe_cmd)
                logger.error(f"Git command failed: {safe_cmd}\n{safe_err}")
                raise RuntimeError(f"Git error: {safe_err}")
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.error("Git command timed out")
            raise

    def clone(self, auth_token: str = None):
        cmd_args = ["clone", self.clone_url, "."]
        if auth_token:
            cmd_args = _auth_header_args(auth_token) + cmd_args

        self.run_git_command(cmd_args, timeout=300, obscure_token=auth_token)

    def checkout_branch(self, branch_name: str, create: bool = False):
        if create:
            self.run_git_command(["checkout", "-b", branch_name])
        else:
            self.run_git_command(["checkout", branch_name])

    def changed_files(self) -> list[str]:
        """Paths that differ from HEAD, including new untracked files (repo-relative)."""
        out = self.run_git_command(["status", "--porcelain", "-z", "--untracked-files=all"])
        files, entries = [], out.split("\0")
        i = 0
        while i < len(entries):
            entry = entries[i]
            i += 1
            if len(entry) < 4:
                continue
            status, path = entry[:2], entry[3:]
            if "R" in status or "C" in status:
                i += 1  # the original path of a rename follows
            files.append(path)
        return files

    def stage_files(self, files: list[str]):
        # Secure individual staging limits avoiding arbitrary wildcards natively!
        for f in files:
            self.run_git_command(["add", "-A", "--", f])

    def commit(self, message: str):
        # Explicit identity: the host may have no global git user configured.
        self.run_git_command([
            "-c", f"user.name={settings.GIT_AUTHOR_NAME}", "-c", f"user.email={settings.GIT_AUTHOR_EMAIL}",
            "commit", "-m", message,
        ])

    def push_branch(self, auth_token: str, branch_name: str):
        # Authenticate with a header (as for clone) rather than embedding the
        # token in the remote URL, where it would show up in process listings.
        self.run_git_command(
            _auth_header_args(auth_token) + ["push", self.clone_url, f"HEAD:refs/heads/{branch_name}"],
            timeout=300, obscure_token=auth_token,
        )

    def cleanup(self):
        shutil.rmtree(self.workspace_path, onerror=_rmtree_onerror)
