import pytest
from unittest.mock import patch, mock_open
from app.services.github_service import GitHubService, GitWorkspace
from app.config import settings

@patch("app.services.github_service.Auth.AppAuth")
@patch("app.services.github_service.Github")
@patch("os.path.exists")
@patch("builtins.open", new_callable=mock_open, read_data="fake_key_content")
def test_github_service_auth(mock_file, mock_exists, mock_github, mock_app_auth, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_APP_ID", "123")
    monkeypatch.setattr(settings, "GITHUB_APP_PRIVATE_KEY", "/fake/path.pem")
    mock_exists.return_value = True
    svc = GitHubService(456)
    mock_app_auth.assert_called()
    assert svc.client is not None

def test_git_workspace_safe_command():
    ws = GitWorkspace("https://github.com/fake/repo.git")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "cloned"
        ws.clone("fake_token")
        
        args, kwargs = mock_run.call_args
        cmd_list = args[0]
        
        assert kwargs["shell"] is False
        assert cmd_list[0] == "git"
        assert cmd_list[1] == "-c"
        
        import base64
        expected_b64 = base64.b64encode(b"x-access-token:fake_token").decode("ascii")
        assert f"http.extraHeader=AUTHORIZATION: Basic {expected_b64}" in cmd_list[2]
        assert cmd_list[3] == "clone"
        
        full_cmd_str = " ".join(cmd_list)
        assert ws.clone_url in cmd_list

def test_git_workspace_no_persisted_token():
    ws = GitWorkspace("https://github.com/fake/repo.git")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "cloned"
        ws.clone("mock_token")
        
        args, kwargs = mock_run.call_args
        cmd_list = args[0]
        
        # Verify Token is NOT in URL (preventing .git/config mapping leaks)
        assert ws.clone_url in cmd_list
        assert "x-access-token" not in ws.clone_url
        assert "mock_token" not in ws.clone_url
        
        # Verify Token is passed via Header correctly
        import base64
        ext_header = f"http.extraHeader=AUTHORIZATION: Basic {base64.b64encode(b'x-access-token:mock_token').decode('ascii')}"
        assert ext_header in cmd_list
