"""POST /repositories/connect must not let one user take over another user's repository."""
import uuid
from unittest.mock import MagicMock

import pytest

from app.api.auth import get_current_user
from app.db.session import SessionLocal
from app.main import app
from app.models.domain import Repository, User


@pytest.fixture
def github(monkeypatch):
    gh = MagicMock()
    monkeypatch.setattr("app.api.repositories.GitHubService", MagicMock(return_value=gh,
                        get_installation_id_for_repo=MagicMock(return_value=1)))
    return gh


def _user(db) -> User:
    user = User(github_user_id=uuid.uuid4().hex, github_login=uuid.uuid4().hex[:8])
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _repo(db, owner_id):
    name = uuid.uuid4().hex[:10]
    repo = Repository(github_repo_id=uuid.uuid4().hex, owner="o", name=name, full_name=f"o/{name}",
                      clone_url="https://example.invalid/x.git", user_id=owner_id)
    db.add(repo)
    db.commit()
    return repo.full_name


def test_cannot_take_over_another_users_repository(client, github):
    db = SessionLocal()
    try:
        alice, mallory = _user(db), _user(db)
        full_name = _repo(db, alice.id)
        app.dependency_overrides[get_current_user] = lambda: mallory
        res = client.post("/repositories/connect", json={"full_name": full_name})
        assert res.status_code == 409
        db.expire_all()
        assert db.query(Repository).filter(Repository.full_name == full_name).one().user_id == alice.id
    finally:
        db.close()


def test_unclaimed_repository_can_be_claimed(client, github):
    db = SessionLocal()
    try:
        bob = _user(db)
        full_name = _repo(db, None)  # created by a webhook, nobody connected it yet
        app.dependency_overrides[get_current_user] = lambda: bob
        res = client.post("/repositories/connect", json={"full_name": full_name})
        assert res.status_code == 200 and res.json()["full_name"] == full_name
        db.expire_all()
        assert db.query(Repository).filter(Repository.full_name == full_name).one().user_id == bob.id
    finally:
        db.close()
