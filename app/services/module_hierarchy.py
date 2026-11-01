"""Business modules group existing entitlements without changing stored codes."""

MODULE_GROUPS = [
    {"code": "sd", "label": "SD · Ventas y distribución", "modules": ["sales", "customers", "quotes", "sales_orders", "pos"]},
    {"code": "mm", "label": "MM · Materiales y abastecimiento", "modules": ["purchases", "suppliers", "purchase_orders", "products"]},
    {"code": "wm", "label": "WM · Gestión de almacenes", "modules": ["inventory"]},
    {"code": "fi", "label": "FI · Finanzas y contabilidad", "modules": ["finance", "reconciliation", "operations"]},
    *[{"code": short, "label": label, "modules": [code]} for short, code, label in [
        ("hcm", "hcm", "HCM · Personas y organización"), ("pp", "production", "PP · Producción"),
        ("pm", "maintenance", "PM · Mantenimiento"), ("qm", "quality", "QM · Calidad"),
        ("tm", "transport", "TM · Transporte"), ("ps", "projects", "PS · Proyectos"),
        ("co", "controlling", "CO · Control de gestión"), ("aa", "assets", "AA · Activos fijos"),
        ("crm", "crm", "CRM · Gestión comercial"), ("ehs", "safety", "EHS · Seguridad y ambiente"),
        ("cs", "service", "CS · Servicio al cliente"), ("dms", "documents", "DMS · Documentos"),
        ("bi", "reports", "BI · Análisis e informes"), ("gobierno", "administration", "Administración del propietario"),
    ]],
]

# Shared materials stay independently available for authorized stock/sales lookups.
# Configuration/accounting share the legacy operations entitlement, so it is not
# subordinated to treasury. Permission checks distinguish governance from accounting.
MODULE_PARENTS = {"customers": "sales", "quotes": "sales", "sales_orders": "sales", "pos": "sales", "suppliers": "purchases", "purchase_orders": "purchases", "reconciliation": "finance"}

# Shared lookups belong to the user's enabled business workspace too.
ROLE_WORKSPACES = {"sales": "sales", "purchases": "purchases", "inventory": "inventory", "finance": "finance", "hr": "hcm", "production": "production", "maintenance": "maintenance", "quality": "quality", "transport": "transport", "projects": "projects", "controller": "controlling", "assets": "assets", "crm": "crm", "safety": "safety", "service": "service"}


def entitlement_enabled(code, company, personal, blocked):
    required = [code, *([MODULE_PARENTS[code]] if code in MODULE_PARENTS else [])]
    return all(item not in blocked and company.get(item, True) and personal.get(item, True) for item in required)
