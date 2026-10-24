from urllib.parse import parse_qs, urlparse

from app.api import auth


def test_login_state_is_single_use_and_expires(client, monkeypatch):
    monkeypatch.setattr(auth.settings, "GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth, "oauth_states", {})
    res = client.get("/auth/github/login", follow_redirects=False)
    assert res.status_code == 302
    query = parse_qs(urlparse(res.headers["location"]).query)
    state = query["state"][0]
    assert query["redirect_uri"] == [auth.settings.GITHUB_OAUTH_REDIRECT_URI]

    auth.oauth_states[state] -= auth.OAUTH_STATE_TTL_SECONDS + 1  # pretend the login was abandoned
    assert client.get(f"/auth/github/callback?code=x&state={state}").status_code == 400
    assert state not in auth.oauth_states

    auth.oauth_states["stale"] = 0.0
    client.get("/auth/github/login", follow_redirects=False)
    assert "stale" not in auth.oauth_states  # old entries are pruned on the next login
