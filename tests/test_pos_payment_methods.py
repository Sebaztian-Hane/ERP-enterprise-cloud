from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.payments import POS_PAYMENT_METHODS, PosPaymentMethodConfigUpsert


def test_pos_payment_method_config_accepts_only_supported_tenders():
    config = PosPaymentMethodConfigUpsert(
        method="yape",
        cash_account_id=uuid4(),
        is_active=True,
    )

    assert config.method == "yape"
    assert POS_PAYMENT_METHODS == frozenset({"cash", "visa", "mastercard", "yape", "plin"})


def test_pos_payment_method_config_rejects_unknown_tender():
    with pytest.raises(ValidationError, match="pago POS no compatible"):
        PosPaymentMethodConfigUpsert(method="transferencia", cash_account_id=uuid4())


def test_pos_payment_method_migration_enforces_one_config_per_tenant_and_tender():
    migration = Path("alembic/versions/0061_pos_payment_methods.sql").read_text(encoding="utf-8")

    assert "unique(tenant_id, method)" in migration.lower()
    assert "references public.cash_accounts(id)" in migration.lower()


def test_pos_cash_account_lookup_is_scoped_to_the_active_tenant():
    source = Path("app/api/routes/payments.py").read_text(encoding="utf-8")

    assert "from cash_accounts where id=:id and tenant_id=:tenant and is_active=true" in source


def test_pos_cash_account_lookup_requires_pen_for_pen_pos_sales():
    source = Path("app/api/routes/payments.py").read_text(encoding="utf-8")

    assert "currency='PEN'" in source


def test_pos_method_read_authorizes_counter_staff_and_finance_without_granting_configuration_write():
    source = Path("app/api/routes/payments.py").read_text(encoding="utf-8")

    assert "def require_pos_methods_read" in source
    assert '"sales:read" not in granted and "payments:read" not in granted' in source
    assert 'require_permission("payments:write")' in source
