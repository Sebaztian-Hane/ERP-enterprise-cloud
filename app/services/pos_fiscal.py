"""Fiscal validation shared by the POS command and CPE reservation flow."""

from decimal import Decimal

from fastapi import HTTPException


POS_IDENTIFICATION_THRESHOLD = Decimal("700.00")
POS_OPTIONAL_RECEIPT_THRESHOLD = Decimal("5.00")


def _valid_document(value: str | None) -> bool:
    return value is not None and value.isdigit() and len(value) in {8, 11}


def validate_administrative_sale_customer(*, customer_tax_id: str | None) -> None:
    """Administrative/wholesale sales always need a traceable counterparty."""
    if not _valid_document((customer_tax_id or "").strip()):
        raise HTTPException(
            status_code=422,
            detail="Las ventas administrativas requieren un cliente con DNI o RUC válido",
        )


def validate_pos_fiscal_selection(
    *, total: Decimal, document_type: str, customer_tax_id: str | None,
) -> None:
    """Apply POS CPE eligibility without ever substituting a fake document.

    ``ticket`` is the internal operational receipt, never a guía de remisión
    or a fiscal CPE.  A caller may use it at any amount; the UI is responsible
    for explaining when the buyer requested a fiscal document.
    """
    normalized_tax_id = (customer_tax_id or "").strip()
    if document_type == "ticket":
        return
    if document_type == "01":
        if not (normalized_tax_id.isdigit() and len(normalized_tax_id) == 11):
            raise HTTPException(status_code=422, detail="Para emitir Factura el cliente debe tener un RUC de 11 dígitos")
        return
    if document_type != "03":
        raise HTTPException(status_code=422, detail="Tipo de comprobante POS no compatible")
    if total > POS_IDENTIFICATION_THRESHOLD and not _valid_document(normalized_tax_id):
        raise HTTPException(
            status_code=422,
            detail="Una Boleta POS superior a S/ 700 requiere DNI o RUC y nombre del cliente",
        )


def cpe_customer_payload(
    *,
    total: Decimal,
    document_type: str,
    is_pos_receipt: bool,
    customer_tax_id: str | None,
    customer_name: str,
    customer_address: str,
) -> dict[str, str] | None:
    """Build the buyer branch for a CPE without fabricating identification.

    A normal administrative sale still requires an identified buyer.  The one
    anonymous branch is a POS boleta within the legal threshold, represented
    as ``None`` so the UBL builder omits the party entirely.
    """
    tax_id = (customer_tax_id or "").strip()
    if document_type == "01":
        if not (tax_id.isdigit() and len(tax_id) == 11):
            raise HTTPException(status_code=422, detail="Para emitir Factura el cliente debe tener un RUC de 11 dígitos")
    elif document_type == "03":
        if not _valid_document(tax_id):
            if not is_pos_receipt:
                raise HTTPException(status_code=422, detail="Para emitir Boleta el cliente debe tener DNI de 8 dígitos o RUC de 11 dígitos")
            validate_pos_fiscal_selection(total=total, document_type=document_type, customer_tax_id=None)
            return None
        if is_pos_receipt:
            validate_pos_fiscal_selection(total=total, document_type=document_type, customer_tax_id=tax_id)
    else:
        raise HTTPException(status_code=422, detail="Tipo de comprobante electrónico no compatible")
    return {"tax_id": tax_id, "name": customer_name, "address": customer_address}
