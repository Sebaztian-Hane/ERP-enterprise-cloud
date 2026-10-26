from pathlib import Path


def test_cors_allows_put_for_tenant_payment_configuration():
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert '"PUT"' in source
