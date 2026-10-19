"""Server-side DNI/RUC lookup through DeColecta. The key never reaches clients."""
from typing import Any
import httpx
from app.core.config import Settings


class DecolectaError(Exception):
    pass


def lookup_tax_id(settings: Settings, tax_id: str) -> dict[str, str]:
    if not settings.decolecta_api_key:
        raise DecolectaError("La consulta DNI/RUC aún no está configurada en el servidor")
    if len(tax_id) not in {8, 11} or not tax_id.isdigit():
        raise DecolectaError("Ingresa un DNI de 8 dígitos o un RUC de 11 dígitos")
    endpoint = "/v1/reniec/dni" if len(tax_id) == 8 else "/v1/sunat/ruc"
    try:
        with httpx.Client(timeout=12) as client:
            response = client.get(f"{str(settings.decolecta_base_url).rstrip('/')}{endpoint}", params={"numero": tax_id}, headers={"Authorization": f"Bearer {settings.decolecta_api_key}", "Content-Type": "application/json"})
            response.raise_for_status()
            data: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DecolectaError("No fue posible consultar el documento en DeColecta") from exc
    name = str(data.get("full_name") or data.get("razon_social") or "").strip()
    if not name:
        raise DecolectaError(str(data.get("message") or "DeColecta no devolvió datos para el documento"))
    return {"tax_id": tax_id, "name": name, "address": str(data.get("direccion") or "").strip(), "document_type": "DNI" if len(tax_id) == 8 else "RUC"}
