import hmac
import hashlib
import json
from app.config import settings

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_invalid_webhook_signature(client):
    response = client.post("/webhooks/github")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid signature"