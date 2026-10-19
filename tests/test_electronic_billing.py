import json
from decimal import Decimal

from app.services.apisunat import build_document_body


def test_cpe_payload_is_json_serializable_with_decimal_sale_values():
    body = build_document_body(
        sale_number="V-TEST-001",
        issue_date="2026-10-19",
        issue_time="12:00:00",
        document_type="03",
        series="B001",
        correlative="00000001",
        issuer={"ruc": "10406430711", "trade_name": "Empresa", "legal_name": "Empresa SAC", "address": "Lima"},
        customer={"tax_id": "74184767", "name": "Cliente", "address": "Lima"},
        lines=[{"quantity": Decimal("7"), "unit_price": Decimal("45.00"), "unit": "unit", "description": "Malla soldada"}],
        total=Decimal("315.00"),
    )

    encoded = json.dumps({"documentBody": body})

    assert '"_text": 7' in encoded
    assert body["cac:InvoiceLine"][0]["cbc:InvoicedQuantity"]["_text"] == 7
