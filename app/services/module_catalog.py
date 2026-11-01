"""Existing entitlement codes; new screens reuse their business module."""

from app.services.enterprise_roles import ADDITIONAL_MODULES

MODULES = [
    ("sales", "Ventas"), ("pos", "Punto de venta"), ("customers", "Clientes"), ("quotes", "Cotizaciones"),
    ("sales_orders", "Pedidos de venta"), ("purchases", "Compras"),
    ("suppliers", "Proveedores"), ("purchase_orders", "Órdenes de compra"),
    ("inventory", "Inventario"), ("products", "Productos"), ("finance", "Finanzas"),
    ("reconciliation", "Conciliación"), ("documents", "Documentos"), ("reports", "Reportes"),
    ("operations", "Control operativo"), ("administration", "Administración"),
]
MODULES += ADDITIONAL_MODULES
