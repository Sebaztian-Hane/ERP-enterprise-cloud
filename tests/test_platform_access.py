from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.security.auth import platform_actor_from_claims
from app.security.auth import PlatformActor


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


def test_platform_ruc_lookup_reuses_server_side_decolecta(monkeypatch):
    """The provider console must receive legal name/address without a browser API key."""
    from app.api.routes.platform import lookup_platform_tenant_ruc

    expected = {
        "tax_id": "20100055237",
        "name": "EMPRESA DEMO S.A.C.",
        "address": "Av. Principal 123, Lima",
        "document_type": "RUC",
    }
    monkeypatch.setattr("app.api.routes.platform.lookup_tax_id", lambda settings, tax_id: expected)
    monkeypatch.setattr("app.api.routes.platform.get_settings", lambda: object())

    result = lookup_platform_tenant_ruc.__wrapped__(
        request=None,
        response=None,
        tax_id="20100055237",
        actor=PlatformActor(user_id=uuid4(), email="provider@example.com"),
    )

    assert result == expected


@pytest.mark.asyncio
async def test_platform_owner_credentials_are_sent_by_email(monkeypatch):
    from app.api.routes import platform

    delivered: dict[str, str] = {}

    async def capture_email(settings, *, recipient, subject, html):
        delivered.update(recipient=recipient, subject=subject, html=html)

    monkeypatch.setattr(platform, "send_brevo_email", capture_email)

    await platform.send_platform_owner_credentials(
        object(),
        owner_email="owner@example.com",
        tenant_name="Empresa de prueba",
        temporary_password="Temporal123!",
    )

    assert delivered["recipient"] == "owner@example.com"
    assert delivered["subject"] == "Tus credenciales temporales de EnterpriseCloud"
    assert "Empresa de prueba" in delivered["html"]
    assert "owner@example.com" in delivered["html"]
    assert "Temporal123!" in delivered["html"]
