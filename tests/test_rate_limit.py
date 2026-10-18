from fastapi.testclient import TestClient

from app.main import app


def test_health_is_available_without_authentication():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"


def test_cors_only_advertises_methods_used_by_the_api():
    response = TestClient(app).options(
        "/api/v1/auth/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    allowed_methods = response.headers["access-control-allow-methods"]
    assert "GET" in allowed_methods
    assert "POST" in allowed_methods
    assert "PATCH" in allowed_methods
    assert "DELETE" not in allowed_methods
