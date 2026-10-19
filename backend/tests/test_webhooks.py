import json
import hmac
import hashlib
from app.config import settings

def test_ignored_unsupported_webhook(client):
    payload_bytes = json.dumps({"action": "labeled"}).encode('utf-8')
    mac = hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode('utf-8'), msg=payload_bytes, digestmod=hashlib.sha256)
    
    response = client.post(
        "/webhooks/github",
        content=payload_bytes,
        headers={"x-hub-signature-256": "sha256=" + mac.hexdigest(), "x-github-event": "issues"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"

def test_webhook_unsupported_event_type(client):
    payload_bytes = b'{}'
    mac = hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode('utf-8'), msg=payload_bytes, digestmod=hashlib.sha256)
    
    response = client.post(
        "/webhooks/github",
        content=payload_bytes,
        headers={"x-hub-signature-256": "sha256=" + mac.hexdigest(), "x-github-event": "push"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


# ---------------------------------------------------------------------------
# Issue ingestion
# ---------------------------------------------------------------------------
import uuid
from unittest.mock import patch
from app.db.session import SessionLocal
from app.models.domain import Issue


def _issue_event(action="opened", installation=True, **issue_overrides):
    rid = uuid.uuid4().int % 10**9
    issue = {"id": rid + 1, "number": 5, "title": "Bug", "body": "details", "state": "open",
             "labels": [{"name": "bug"}], **issue_overrides}
    payload = {"action": action, "issue": issue,
               "repository": {"id": rid, "name": "n", "full_name": f"o/n{rid}", "owner": {"login": "o"},
                              "clone_url": "https://github.com/o/n.git", "default_branch": "main"}}
    if installation:
        payload["installation"] = {"id": 99}
    return payload


def _post(client, payload, delivery=None):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {"x-hub-signature-256": sig, "x-github-event": "issues"}
    if delivery:
        headers["x-github-delivery"] = delivery
    return client.post("/webhooks/github", content=body, headers=headers)


@patch("app.api.webhooks._first_delivery", return_value=True)
def test_opened_issue_creates_and_enqueues_run(_dedupe, client):
    payload = _issue_event()
    with patch("app.services.run_service.queue_service.enqueue_issue_job") as enqueue:
        res = _post(client, payload)
    assert res.status_code == 200 and res.json()["status"] == "accepted"
    run_id = res.json()["agent_run_id"]
    enqueue.assert_called_once_with({"agent_run_id": run_id, "installation_id": 99})
    db = SessionLocal()
    issue = db.query(Issue).filter(Issue.github_issue_id == str(payload["issue"]["id"])).one()
    assert issue.labels == "bug"
    # A second "opened" for the same issue does not start a duplicate run.
    with patch("app.services.run_service.queue_service.enqueue_issue_job") as enqueue:
        again = _post(client, payload)
    assert again.json()["status"] == "ignored"
    enqueue.assert_not_called()


@patch("app.api.webhooks._first_delivery", return_value=True)
def test_edited_issue_updates_body(_dedupe, client):
    payload = _issue_event(action="edited")
    _post(client, payload)
    payload["issue"]["body"] = "new details"
    res = _post(client, payload)
    assert res.json()["status"] == "updated"
    db = SessionLocal()
    assert db.query(Issue).filter(Issue.github_issue_id == str(payload["issue"]["id"])).one().body == "new details"


def test_redelivered_event_is_ignored(client):
    with patch("app.api.webhooks.queue_service.redis_client") as redis:
        redis.set.return_value = None  # key already present
        res = _post(client, _issue_event(), delivery="abc-123")
    assert res.json() == {"status": "ignored", "reason": "duplicate delivery"}


@patch("app.api.webhooks._first_delivery", return_value=True)
def test_pull_request_issue_events_are_ignored(_dedupe, client):
    res = _post(client, _issue_event(pull_request={"url": "x"}))
    assert res.json()["reason"] == "pull request event"


@patch("app.api.webhooks._first_delivery", return_value=True)
def test_event_without_installation_is_not_queued(_dedupe, client):
    with patch("app.services.run_service.queue_service.enqueue_issue_job") as enqueue:
        res = _post(client, _issue_event(installation=False))
    assert res.json()["status"] == "ignored"
    enqueue.assert_not_called()


@patch("app.api.webhooks._first_delivery", return_value=True)
def test_enqueue_failure_returns_503(_dedupe, client):
    with patch("app.services.run_service.queue_service.enqueue_issue_job", side_effect=ConnectionError("down")):
        res = _post(client, _issue_event())
    assert res.status_code == 503


def test_webhook_rejected_when_secret_not_configured(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", None)
    res = client.post("/webhooks/github", content=b"{}", headers={"x-github-event": "issues"})
    assert res.status_code == 503