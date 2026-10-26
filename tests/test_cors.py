from fastapi.testclient import TestClient

from app.main import app


@app.get("/_test-cors-error")
def cors_error_route():
    raise RuntimeError("intentional CORS regression test")


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


def test_unhandled_error_keeps_cors_header_for_browser_clients():
    response = TestClient(app, raise_server_exceptions=False).get(
        "/_test-cors-error",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
