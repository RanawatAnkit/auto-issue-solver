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