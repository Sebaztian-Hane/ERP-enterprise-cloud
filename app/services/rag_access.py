"""Role-scoped access policy for the ERP assistant.

This policy is deliberately enforced before a question reaches the external
RAG workflow. The workflow also receives the allowed sources as a second
defence, but it must never be the only place where authorization is decided.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssistantScope:
    label: str
    allowed_topics: tuple[str, ...]
    allowed_sources: tuple[str, ...]


_ALL_SOURCES = (
    "erp_guide", "product_catalog", "inventory_stock", "customer", "supplier",
    "warehouse", "sale", "purchase", "receivable", "payable", "payment",
    "cash_account", "bank_statement", "reconciliation", "accounting_entry", "report",
)

_SCOPES: dict[str, AssistantScope] = {
    "owner": AssistantScope("Administración total", ("all",), _ALL_SOURCES),
    "admin": AssistantScope("Administración total", ("all",), _ALL_SOURCES),
    "sales": AssistantScope(
        "Ventas", ("sales", "customers", "catalog", "electronic_billing", "documents"),
        ("erp_guide", "product_catalog", "customer", "sale"),
    ),
    "purchases": AssistantScope(
        "Compras", ("purchases", "suppliers", "catalog"),
        ("erp_guide", "product_catalog", "supplier", "purchase"),
    ),
    "inventory": AssistantScope(
        "Inventario", ("inventory", "catalog", "warehouses"),
        ("erp_guide", "product_catalog", "inventory_stock", "warehouse"),
    ),
    "finance": AssistantScope(
        "Finanzas y contabilidad", ("finance", "sales", "purchases", "reports", "documents"),
        ("erp_guide", "product_catalog", "sale", "purchase", "receivable", "payable", "payment", "cash_account", "bank_statement", "reconciliation", "accounting_entry", "report"),
    ),
    "viewer": AssistantScope(
        "Consulta", ("sales", "purchases", "inventory", "catalog", "customers", "suppliers", "reports", "documents"),
        ("erp_guide", "product_catalog", "inventory_stock", "customer", "supplier", "warehouse", "sale", "purchase", "receivable", "payable", "report"),
    ),
}

# The phrases represent data domains, not just navigation labels. A match is
# intentionally conservative: mixed requests are denied as a whole, so the
# model cannot answer the restricted half by accident.
_TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "inventory": ("inventari", "stock", "existencias", "kardex", "lote", "serie", "movimiento de almac", "movimiento de stock", "movimiento de inventari", "ajuste de inventari", "reserva de stock"),
    "warehouses": ("almacén", "almacen", "transferencia entre almac"),
    "purchases": ("compra", "orden de compra", "proveedor"),
    "suppliers": ("proveedor",),
    "sales": ("venta", "cotización", "cotizacion", "pedido de venta", "descuento"),
    "customers": ("cliente",),
    "finance": ("tesorería", "tesoreria", "flujo de caja", "banco", "conciliación", "conciliacion", "cuenta por cobrar", "cuentas por cobrar", "cuenta por pagar", "cuentas por pagar", "pago", "cobro", "moro", "saldo"),
    "reports": ("reporte", "indicador", "gráfico", "grafico", "insight"),
    "accounting": ("contabilidad", "asiento", "plan de cuentas", "cierre mensual", "cierre contable", "libro contable"),
    "administration": ("usuario", "permiso", "rol", "invitación", "invitacion", "contraseña", "contrasena", "configuración", "configuracion"),
}

_TOPIC_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "inventory": ("inventory",), "warehouses": ("warehouses", "inventory"),
    "purchases": ("purchases",), "suppliers": ("suppliers", "purchases"),
    "sales": ("sales",), "customers": ("customers", "sales"),
    "finance": ("finance",), "reports": ("reports", "finance"),
    "accounting": ("finance",), "administration": ("all",),
}


def assistant_scope(role_code: str) -> AssistantScope:
    """Return a fail-closed scope for a membership role."""
    return _SCOPES.get(role_code, AssistantScope("Sin acceso", (), ()))


def denied_topic(message: str, role_code: str) -> str | None:
    """Return the first restricted business domain mentioned in a request."""
    scope = assistant_scope(role_code)
    if "all" in scope.allowed_topics:
        return None
    text = message.casefold()
    for topic, patterns in _TOPIC_PATTERNS.items():
        if any(pattern in text for pattern in patterns):
            if not any(requirement in scope.allowed_topics for requirement in _TOPIC_REQUIREMENTS[topic]):
                return topic
    return None


def denied_message(role_code: str, topic: str) -> str:
    scope = assistant_scope(role_code)
    return (
        f"Tu rol ({scope.label}) no tiene acceso a información de {topic}. "
        "Puedo ayudarte únicamente con los módulos autorizados para tu rol."
    )
