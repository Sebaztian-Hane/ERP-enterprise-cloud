import asyncio
import json
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import text
from starlette.requests import Request

from app.main import app
from app.security import action_confirmation as security
from app.security.auth import get_user_id
from test_migration_projects import suite, create, upload, simulate


@pytest.fixture
def secured(suite, monkeypatch):
    client, db, state, *_ = suite
    app.dependency_overrides.pop(security.enforce_action_confirmation)
    db.execute(text("""create table idempotency_requests (
        id text primary key, tenant_id text not null, actor_user_id text not null,
        operation text not null, idempotency_key text not null, request_hash text not null,
        status text not null, response_status integer, response_body text, completed_at timestamp,
        unique (tenant_id, actor_user_id, operation, idempotency_key))"""))
    db.commit()
    identity = security.ConfirmationIdentity(state["actor"].user_id, state["actor"].tenant_id, str(uuid4()), "aal2", 1000)
    monkeypatch.setattr(security, "confirmation_identity", lambda *_: identity)
    crypt = Fernet(Fernet.generate_key())
    monkeypatch.setattr(security, "cipher", lambda: crypt)
    clock = {"now": 1100}
    monkeypatch.setattr(security.time, "time", lambda: clock["now"])
    return suite, identity, clock


def command(suite):
    project = simulate(suite, upload(suite, create(suite, ["base"]), "units"))
    return f"/api/v1/migration-projects/{project['id']}/commit", {"version": project["version"], "digest": project["simulation"]["digest"], "confirmed": True}


def request_challenge(client, path, body):
    response = client.post(path, json=body)
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "ACTION_CONFIRMATION_REQUIRED"
    return response.json()["detail"]["challenge"]


def test_aal2_alone_and_refreshed_old_mfa_cannot_commit(secured):
    suite, identity, clock = secured
    path, body = command(suite)
    token = request_challenge(suite[0], path, body)
    assert suite[0].post(path, json=body, headers={"X-Action-Confirmation": token}).status_code == 403
    # Only a signed MFA event strictly after the action challenge qualifies.
    clock["now"] += 2; identity.verified_at = clock["now"]
    result = suite[0].post(path, json=body, headers={"X-Action-Confirmation": token})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "migrated"
    assert suite[1].scalar(text("select count(*) from idempotency_requests")) == 1
    assert suite[0].post(path, json=body, headers={"X-Action-Confirmation": token}).status_code == 403


@pytest.mark.parametrize("change", ["body", "tenant", "user", "session", "aal", "tampered", "expired", "query"])
def test_proof_is_bound_to_command_user_tenant_session_and_expiry(secured, change):
    suite, identity, clock = secured
    path, body = command(suite)
    token = request_challenge(suite[0], path, body)
    clock["now"] += 2; identity.verified_at = clock["now"]
    if change == "body": body["confirmed"] = False
    if change == "tenant": identity.tenant_id = uuid4()
    if change == "user": identity.user_id = uuid4()
    if change == "session": identity.session_id = str(uuid4())
    if change == "aal": identity.aal = "aal1"
    if change == "tampered": token = token[:-5] + "ABCDE"
    if change == "expired": clock["now"] += security.TTL_SECONDS + 1
    if change == "query": path += "?background=true"
    response = suite[0].post(path, json=body, headers={"X-Action-Confirmation": token})
    assert response.status_code == 403, response.text
    assert suite[1].scalar(text("select count(*) from idempotency_requests")) == 0


def test_one_mfa_event_cannot_authorize_two_prepared_commands_even_across_tenants(secured):
    suite, identity, clock = secured
    first = command(suite); second = command(suite)
    token1 = request_challenge(suite[0], *first)
    token2 = request_challenge(suite[0], *second)
    clock["now"] += 2; identity.verified_at = clock["now"]
    assert suite[0].post(first[0], json=first[1], headers={"X-Action-Confirmation": token1}).status_code == 200
    response = suite[0].post(second[0], json=second[1], headers={"X-Action-Confirmation": token2})
    assert response.status_code == 403
    assert "ya se utilizó" in response.json()["detail"]["message"]
    assert not security.consume_confirmation(suite[1], identity, "b" * 64, suite[3])


def test_form_digest_is_stable_across_multipart_boundaries_and_changes_with_file():
    async def digest(boundary, content):
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="Materiales.csv"\r\nContent-Type: text/csv\r\n\r\n{content}\r\n--{boundary}--\r\n').encode()
        async def receive(): return {"type": "http.request", "body": body, "more_body": False}
        request = Request({"type": "http", "method": "POST", "path": "/api/v1/imports/products/commit", "query_string": b"", "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())]}, receive)
        result = await security.command_hash(request)
        assert await (await request.form())["file"].read() == content.encode()
        return result
    assert asyncio.run(digest("AAA", "a,b")) == asyncio.run(digest("BBB", "a,b"))
    assert asyncio.run(digest("AAA", "a,b")) != asyncio.run(digest("AAA", "a,c"))


def test_company_creation_consumes_confirmation_in_the_same_transaction(secured):
    suite, identity, clock = secured
    identity.tenant_id = None
    app.dependency_overrides[get_user_id] = lambda: identity.user_id
    body = {"name": "Empresa confirmada", "timezone": "America/Lima"}
    token = request_challenge(suite[0], "/api/v1/tenants", body)
    clock["now"] += 2; identity.verified_at = clock["now"]
    response = suite[0].post("/api/v1/tenants", json=body, headers={"X-Action-Confirmation": token})
    assert response.status_code == 201, response.text
    assert suite[1].scalar(text("select count(*) from idempotency_requests")) == 1


def test_protection_covers_high_risk_routes_and_leaves_preparation_available():
    for path in ["/migration-projects/id/commit", "/imports/products/commit", "/operations/approvals/id/approve", "/operations/approvals/id/reject", "/enterprise/records/id/transition", "/sales", "/sales/pos-checkout", "/purchases/id/cancel", "/payments/id/execute", "/inventory/adjustments/id/execute", "/treasury/movements", "/tenants/memberships/id", "/platform/tenants/id/modules/batch"]:
        assert security.protected_action("POST", "/api/v1" + path), path
        assert not security.protected_action("GET", "/api/v1" + path)
    for path in ["/migration-projects/id/simulate", "/migration-projects/id/upload-bulk", "/migration-projects/id/objects/products/file", "/enterprise/employees/records", "/enterprise/records/id/edit", "/imports/products/preview", "/documents/id/signed-url"]:
        assert not security.protected_action("POST", "/api/v1" + path), path


def test_verified_confirmation_never_grants_a_missing_role(secured):
    suite, identity, clock = secured
    path, body = command(suite)
    token = request_challenge(suite[0], path, body)
    suite[2]["actor"] = suite[2]["actor"].model_copy(update={"role_code": "viewer"})
    clock["now"] += 2; identity.verified_at = clock["now"]
    response = suite[0].post(path, json=body, headers={"X-Action-Confirmation": token})
    assert response.status_code == 403
    assert "propietario" in response.json()["detail"]
    assert suite[1].scalar(text("select count(*) from idempotency_requests")) == 0


def test_identity_uses_only_verified_claims_not_mutable_user_metadata(monkeypatch):
    user_id, tenant_id, session_id = uuid4(), uuid4(), uuid4()
    claims = {"sub": str(user_id), "session_id": str(session_id), "aal": "aal1", "user_metadata": {"aal": "aal2", "amr": [{"method": "totp", "timestamp": 9999999999}]}}
    monkeypatch.setattr(security, "decode_access_token", lambda token: claims)
    monkeypatch.setattr(security, "get_actor", lambda *_: None)
    monkeypatch.setattr(security, "ensure_token_not_revoked", lambda *_: None)
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/sales", "query_string": b"", "headers": [(b"authorization", b"Bearer verified-test-token"), (b"x-tenant-id", str(tenant_id).encode())]})
    identity = security.confirmation_identity(request, None)
    assert identity.aal == "aal1" and identity.verified_at == 0
    claims.update(aal="aal2", amr=[{"method": "password", "timestamp": 9999999999}, {"method": "totp", "timestamp": 12345}])
    identity = security.confirmation_identity(request, None)
    assert identity.verified_at == 12345 and identity.session_id == str(session_id)
