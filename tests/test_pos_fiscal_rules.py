from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.services.apisunat import build_document_body
from app.services.pos_fiscal import (
    cpe_customer_payload,
    validate_administrative_sale_customer,
    validate_pos_fiscal_selection,
)


def _issuer():
    return {
        "ruc": "20123456789",
        "trade_name": "Tienda demo",
        "legal_name": "Tienda demo S.A.C.",
        "address": "Av. Demo 123",
    }


def _line():
    return [{"quantity": 1, "unit_price": Decimal("50.00"), "unit": "unit", "description": "Producto demo"}]


def test_anonymous_boleta_payload_omits_customer_identification():
    body = build_document_body(
        sale_number="PV-001",
        issue_date="2026-10-26",
        issue_time="12:00:00",
        document_type="03",
        series="B001",
        correlative="00000001",
        issuer=_issuer(),
        customer=None,
        lines=_line(),
        total=Decimal("50.00"),
    )

    assert "cac:AccountingCustomerParty" not in body


def test_boleta_above_seven_hundred_requires_an_identified_customer():
    with pytest.raises(HTTPException, match="S/ 700"):
        validate_pos_fiscal_selection(
            total=Decimal("700.01"),
            document_type="03",
            customer_tax_id=None,
        )


def test_invoice_requires_ruc_and_never_accepts_dni():
    with pytest.raises(HTTPException, match="RUC de 11"):
        validate_pos_fiscal_selection(
            total=Decimal("100.00"),
            document_type="01",
            customer_tax_id="12345678",
        )


def test_low_value_ticket_needs_no_customer_identification():
    validate_pos_fiscal_selection(
        total=Decimal("5.00"),
        document_type="ticket",
        customer_tax_id=None,
    )


def test_administrative_sale_requires_an_identified_customer():
    with pytest.raises(HTTPException, match="DNI o RUC"):
        validate_administrative_sale_customer(customer_tax_id=None)


def test_only_a_pos_boleta_under_threshold_can_omit_the_buyer_payload():
    assert cpe_customer_payload(
        total=Decimal("700.00"),
        document_type="03",
        is_pos_receipt=True,
        customer_tax_id=None,
        customer_name="Consumidor final",
        customer_address="",
    ) is None

    with pytest.raises(HTTPException, match="DNI"):
        cpe_customer_payload(
            total=Decimal("700.01"),
            document_type="03",
            is_pos_receipt=True,
            customer_tax_id=None,
            customer_name="Consumidor final",
            customer_address="",
        )
