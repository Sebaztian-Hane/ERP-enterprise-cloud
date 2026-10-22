from fastapi.testclient import TestClient

from app.main import app


def test_idempotency_header_is_allowed_by_cors_preflight():
    response = TestClient(app).options(
        "/api/v1/sales",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type, x-tenant-id, idempotency-key",
        },
    )

    assert response.status_code == 200
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()
