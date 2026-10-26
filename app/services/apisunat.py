"""Server-only APISUNAT client and UBL 2.1 payload builder for development CPE."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from app.core.config import Settings


PENNY = Decimal("0.01")
UNIT_CODES = {"unit": "NIU", "unidad": "NIU", "kg": "KGM", "g": "GRM", "l": "LTR", "m": "MTR"}


class APISunatError(Exception):
    pass


def _money(value: Decimal) -> Decimal:
    return value.quantize(PENNY, rounding=ROUND_HALF_UP)


def _json_number(value: Decimal | int | float) -> int | float:
    """Return a native JSON number; never leave Decimal values in CPE payloads."""
    decimal_value = Decimal(value)
    if decimal_value == decimal_value.to_integral_value():
        return int(decimal_value)
    return float(decimal_value)


def _under_thousand(number: int) -> str:
    ones = ("", "UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE")
    teens = ("DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE", "DIECISÉIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE")
    tens = ("", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA")
    hundreds = ("", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS", "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS")
    if number == 0:
        return ""
    if number == 100:
        return "CIEN"
    words: list[str] = []
    if number >= 100:
        words.append(hundreds[number // 100])
        number %= 100
    if 10 <= number < 20:
        words.append(teens[number - 10])
    elif number >= 20 and number < 30:
        words.append("VEINTE" if number == 20 else f"VEINTI{ones[number - 20]}")
    else:
        if number >= 30:
            words.append(tens[number // 10] + (f" Y {ones[number % 10]}" if number % 10 else ""))
        elif number:
            words.append(ones[number])
    return " ".join(words)


def amount_in_words(amount: Decimal) -> str:
    """Spanish amount in words accepted by the CPE human-readable note."""
    amount = _money(amount)
    integer = int(amount)
    cents = int((amount - integer) * 100)
    if integer == 0:
        words = "CERO"
    elif integer < 1000:
        words = _under_thousand(integer)
    elif integer < 1_000_000:
        thousands, remainder = divmod(integer, 1000)
        prefix = "MIL" if thousands == 1 else f"{_under_thousand(thousands)} MIL"
        words = f"{prefix} {_under_thousand(remainder)}".strip()
    elif integer < 1_000_000_000:
        millions, remainder = divmod(integer, 1_000_000)
        prefix = "UN MILLÓN" if millions == 1 else f"{_under_thousand(millions)} MILLONES"
        if remainder:
            words = f"{prefix} {amount_in_words(Decimal(remainder)).split(' CON ')[0]}"
        else:
            words = prefix
    else:
        raise APISunatError("El importe excede el máximo admitido para emisión")
    return f"{words} CON {cents:02d}/100 SOLES"


def build_document_body(*, sale_number: str, issue_date: str, issue_time: str, document_type: str, series: str, correlative: str, issuer: dict[str, str], customer: dict[str, str] | None, lines: list[dict[str, Any]], total: Decimal, tax_rate_percent: Decimal = Decimal("18")) -> dict[str, Any]:
    tax_rate_percent = Decimal(tax_rate_percent)
    tax_multiplier = Decimal("1") + tax_rate_percent / Decimal("100")
    taxable_total = Decimal("0")
    tax_total = Decimal("0")
    invoice_lines: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        gross = _money(Decimal(line["quantity"]) * Decimal(line["unit_price"]))
        taxable = _money(gross / tax_multiplier)
        tax = gross - taxable
        taxable_total += taxable
        tax_total += tax
        line_tax_subtotal = {
            "cbc:TaxableAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(taxable)},
            "cbc:TaxAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(tax)},
            "cac:TaxCategory": {
                "cbc:Percent": {"_text": _json_number(tax_rate_percent)},
                "cbc:TaxExemptionReasonCode": {"_text": "10"},
                "cac:TaxScheme": {
                    "cbc:ID": {"_text": "1000"},
                    "cbc:Name": {"_text": "IGV"},
                    "cbc:TaxTypeCode": {"_text": "VAT"},
                },
            },
        }
        invoice_lines.append({
            "cbc:ID": {"_text": index},
            "cbc:InvoicedQuantity": {"_attributes": {"unitCode": UNIT_CODES.get(str(line["unit"]).lower(), "NIU")}, "_text": _json_number(line["quantity"])},
            "cbc:LineExtensionAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(taxable)},
            "cac:PricingReference": {"cac:AlternativeConditionPrice": {"cbc:PriceAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(_money(gross / Decimal(line["quantity"])))}, "cbc:PriceTypeCode": {"_text": "01"}}},
            "cac:TaxTotal": {
                "cbc:TaxAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(tax)},
                "cac:TaxSubtotal": [line_tax_subtotal],
            },
            "cac:Item": {"cbc:Description": {"_text": line["description"]}},
            "cac:Price": {
                "cbc:PriceAmount": {
                    "_attributes": {"currencyID": "PEN"},
                    "_text": float(_money(taxable / Decimal(line["quantity"]))),
                },
            },
        })
    taxable_total, tax_total, total = _money(taxable_total), _money(tax_total), _money(total)
    document = {
        "cbc:UBLVersionID": {"_text": "2.1"}, "cbc:CustomizationID": {"_text": "2.0"},
        "cbc:ID": {"_text": f"{series}-{correlative}"}, "cbc:IssueDate": {"_text": issue_date}, "cbc:IssueTime": {"_text": issue_time},
        "cbc:InvoiceTypeCode": {"_attributes": {"listID": "0101"}, "_text": document_type},
        "cbc:Note": [{"_text": amount_in_words(total), "_attributes": {"languageLocaleID": "1000"}}],
        "cbc:DocumentCurrencyCode": {"_text": "PEN"},
        "cac:AccountingSupplierParty": {"cac:Party": {"cac:PartyIdentification": {"cbc:ID": {"_attributes": {"schemeID": "6"}, "_text": issuer["ruc"]}}, "cac:PartyName": {"cbc:Name": {"_text": issuer["trade_name"]}}, "cac:PartyLegalEntity": {"cbc:RegistrationName": {"_text": issuer["legal_name"]}, "cac:RegistrationAddress": {"cbc:AddressTypeCode": {"_text": "0000"}, "cac:AddressLine": {"cbc:Line": {"_text": issuer["address"]}}}}}},
        "cac:TaxTotal": {"cbc:TaxAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(tax_total)}, "cac:TaxSubtotal": [{"cbc:TaxableAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(taxable_total)}, "cbc:TaxAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(tax_total)}, "cac:TaxCategory": {"cac:TaxScheme": {"cbc:ID": {"_text": "1000"}, "cbc:Name": {"_text": "IGV"}, "cbc:TaxTypeCode": {"_text": "VAT"}}}}]},
        "cac:LegalMonetaryTotal": {"cbc:LineExtensionAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(taxable_total)}, "cbc:TaxInclusiveAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(total)}, "cbc:PayableAmount": {"_attributes": {"currencyID": "PEN"}, "_text": float(total)}},
        "cac:PaymentTerms": [{"cbc:ID": {"_text": "FormaPago"}, "cbc:PaymentMeansID": {"_text": "Contado"}}],
        "cac:InvoiceLine": invoice_lines,
    }
    if customer is not None:
        customer_scheme = "6" if document_type == "01" else ("1" if len(customer["tax_id"]) == 8 else "6")
        document["cac:AccountingCustomerParty"] = {"cac:Party": {"cac:PartyIdentification": {"cbc:ID": {"_attributes": {"schemeID": customer_scheme}, "_text": customer["tax_id"]}}, "cac:PartyLegalEntity": {"cbc:RegistrationName": {"_text": customer["name"]}, "cac:RegistrationAddress": {"cac:AddressLine": {"cbc:Line": {"_text": customer["address"] or "-"}}}}}}
    return document


def _post(settings: Settings, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(f"{str(settings.apisunat_base_url).rstrip('/')}{path}", json=payload)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise APISunatError("No se pudo comunicar con APISUNAT") from exc
    if not isinstance(data, dict) or data.get("status") == "ERROR":
        raise APISunatError(str(data.get("error") if isinstance(data, dict) else "Respuesta inválida de APISUNAT"))
    return data


def next_correlative(settings: Settings, document_type: str, series: str, *, persona_id: str, persona_token: str) -> str:
    data = _post(settings, "/personas/lastDocument", {"personaId": persona_id, "personaToken": persona_token, "type": document_type, "serie": series})
    value = str(data.get("suggestedNumber") or "")
    if not value.isdigit():
        raise APISunatError("APISUNAT no entregó un correlativo válido")
    return value.zfill(8)


def send_bill(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    return _post(settings, "/personas/v1/sendBill", payload)


def get_document(settings: Settings, document_id: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(f"{str(settings.apisunat_base_url).rstrip('/')}/documents/{document_id}/getById")
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise APISunatError("No se pudo consultar el comprobante en APISUNAT") from exc
    if not isinstance(data, dict):
        raise APISunatError("Respuesta inválida de APISUNAT")
    return data


def void_bill(settings: Settings, document_id: str, reason: str, *, persona_id: str, persona_token: str) -> dict[str, Any]:
    return _post(settings, "/personas/v1/voidBill", {"personaId": persona_id, "personaToken": persona_token, "documentId": document_id, "reason": reason})
