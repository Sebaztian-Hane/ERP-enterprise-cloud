from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.security.auth import platform_actor_from_claims


class Settings:
    platform_admin_email_set = {"provider@example.com"}


def test_platform_access_requires_configured_provider_email(monkeypatch):
    monkeypatch.setattr("app.security.auth.get_settings", lambda: Settings())
    actor = platform_actor_from_claims({"sub": str(uuid4()), "email": "provider@example.com"})
    assert actor.email == "provider@example.com"


def test_platform_access_rejects_other_authenticated_users(monkeypatch):
    monkeypatch.setattr("app.security.auth.get_settings", lambda: Settings())
    with pytest.raises(HTTPException) as error:
        platform_actor_from_claims({"sub": str(uuid4()), "email": "member@example.com"})
    assert error.value.status_code == 403
