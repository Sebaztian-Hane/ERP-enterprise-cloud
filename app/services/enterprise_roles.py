"""Additional job roles. Existing seven role codes remain unchanged."""

SPECIALIZED_ROLES = {
    "hr": ("Recursos humanos", "hcm"),
    "maintenance": ("Mantenimiento", "maintenance"),
    "transport": ("Transporte", "transport"),
    "production": ("Producción", "production"),
    "quality": ("Calidad", "quality"),
    "projects": ("Proyectos", "projects"),
    "controller": ("Control de gestión", "controlling"),
    "assets": ("Activos fijos", "assets"),
    "crm": ("Gestión comercial", "crm"),
    "safety": ("Seguridad y ambiente", "safety"),
    "service": ("Servicio al cliente", "service"),
}

ADDITIONAL_MODULES = [(code, label) for label, code in SPECIALIZED_ROLES.values()]
ADDITIONAL_PERMISSIONS = {
    role: {f"{module}:{action}" for action in ("read", "write", "approve")}
    for role, (_, module) in SPECIALIZED_ROLES.items()
}


def extend_permissions(permissions, modules):
    permissions.update({role: set(grants) for role, grants in ADDITIONAL_PERMISSIONS.items()})
    # New namespaces follow the responsibilities of existing job roles; no
    # existing financial, stock, security or fiscal permission is broadened.
    # A specialty is assigned through its own role; sharing a business process
    # does not grant access to another department's registers or workspaces.
    permissions["viewer"].update({f"{code}:read" for _, code in SPECIALIZED_ROLES.values() if code not in {"hcm", "safety"}})
    for _, code in SPECIALIZED_ROLES.values(): modules[code] = code
