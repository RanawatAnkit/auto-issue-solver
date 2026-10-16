from github import Github, Auth, GithubException
from app.config import settings
import subprocess
import tempfile
import logging
import os
import re

logger = logging.getLogger(__name__)

class GitHubService:
    def __init__(self, installation_id: int):
        self.installation_id = installation_id
        if not settings.GITHUB_APP_ID or not settings.GITHUB_APP_PRIVATE_KEY:
            raise ValueError("GitHub App credentials not configured")
            
        if not os.path.exists(settings.GITHUB_APP_PRIVATE_KEY):
            raise ValueError("GitHub App private key file does not exist")
            
        try:
            with open(settings.GITHUB_APP_PRIVATE_KEY, 'r', encoding='utf-8') as f:
                private_key = f.read()
        except Exception:
            raise ValueError("Could not read GitHub App private key file")
        
        self.auth = Auth.AppAuth(
            settings.GITHUB_APP_ID, 
            private_key
        ).get_installation_auth(installation_id)
        self.client = Github(auth=self.auth)

    @classmethod
    def get_installation_id_for_repo(cls, repo_full_name: str) -> int:
        from app.config import settings
        import jwt, time, requests
        with open(settings.GITHUB_APP_PRIVATE_KEY, 'r', encoding='utf-8') as f:
            private_key = f.read()

        now = int(time.time())
        encoded_jwt = jwt.encode({
            'iat': now - 60,
            'exp': now + (8 * 60),
            'iss': settings.GITHUB_APP_ID
        }, private_key, algorithm='RS256')
        
        res = requests.get(f"https://api.github.com/repos/{repo_full_name}/installation", headers={
            'Authorization': 'Bearer ' + encoded_jwt,
            'Accept': 'application/vnd.github.v3+json'
        }, timeout=10)
        
        if res.status_code == 200:
            return res.json()['id']
        if res.status_code == 404:
            owner = repo_full_name.split('/')[0] if '/' in repo_full_name else repo_full_name
            raise ValueError(f"GitHub App is not installed on account '{owner}' or does not have access to '{repo_full_name}'. Please install it at https://github.com/apps/auto-issue-solver-college/installations/new")
        raise ValueError(f"GitHub App installation lookup failed for repository {repo_full_name}. Status: {res.status_code}")

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
             if str(issue_number) in pr.title or str(issue_number) in pr.body:
                 return pr
        return None

def validate_no_secrets(files_content: list[str]) -> bool:
    patterns = [r'(?i)api[_-]?key', r'ghp_[a-zA-Z0-9]{36}', r'-----BEGIN (RSA|OPENSSH) PRIVATE KEY-----', r'(?i)aws[_-]?access[_-]?key']
    for content in files_content:
        for p in patterns:
            if re.search(p, content):
                return False
    return True

class GitWorkspace:
    def __init__(self, clone_url: str):
        self.clone_url = clone_url
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_path = self.temp_dir.name

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
                if obscure_token:
                    safe_cmd = safe_cmd.replace(obscure_token, "***")
                logger.error(f"Git command failed: {safe_cmd}\\n{result.stderr}")
                raise RuntimeError(f"Git error: {result.stderr}")
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.error("Git command timed out")
            raise

    def clone(self, auth_token: str = None):
        cmd_args = ["clone", self.clone_url, "."]
        if auth_token:
            import base64
            auth_string = f"x-access-token:{auth_token}"
            b64_auth = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
            cmd_args = ["-c", f"http.extraHeader=AUTHORIZATION: Basic {b64_auth}"] + cmd_args
            
        self.run_git_command(cmd_args, obscure_token=auth_token)

    def checkout_branch(self, branch_name: str, create: bool = False):
        if create:
            self.run_git_command(["checkout", "-b", branch_name])
        else:
            self.run_git_command(["checkout", branch_name])

    def stage_files(self, files: list[str]):
        # Secure individual staging limits avoiding arbitrary wildcards natively!
        for f in files:
            self.run_git_command(["add", "--", f])

    def commit(self, message: str):
        self.run_git_command(["commit", "-m", message])
        
    def push_branch(self, auth_token: str, branch_name: str):
        # Prevent shell traces by mapping URLs explicitly inside isolated subprocess lists!
        remote = self.clone_url.replace("https://", f"https://x-access-token:{auth_token}@")
        self.run_git_command(["push", "-u", remote, branch_name], obscure_token=auth_token)

    def cleanup(self):
        self.temp_dir.cleanup()
