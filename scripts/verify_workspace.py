"""Read-only SQL smoke check using the authorized backend connection.

Checks query compatibility, not browser authentication. Never logs rows,
identities, connection strings or credentials, and cannot commit business data.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.routes import imports, operations
from app.db.session import engine
from app.models.entities import Tenant, TenantMembership
from app.security.auth import Actor
from app.services.workspace_access import workspace_access


def main():
    checks = [
        ("acceso efectivo", workspace_access),
        ("flujo de caja", operations.cash_flow),
        ("antigüedad de saldos", operations.aging),
        ("libro diario", operations.journal),
        ("balance de comprobación", operations.trial_balance),
        ("disponibilidad", operations.inventory_availability),
        ("valoración", operations.inventory_valuation),
        ("reservas", operations.list_reservations),
        ("aprobaciones", operations.list_approvals),
        ("políticas", operations.approval_policies),
        ("plantillas disponibles", imports.list_templates),
    ]
    current = "conexión"
    try:
        with engine.connect() as connection:
            connection.execute(text("set transaction read only"))
            with Session(bind=connection) as db:
                member = db.scalar(select(TenantMembership).join(Tenant).where(TenantMembership.is_active.is_(True), Tenant.is_active.is_(True), TenantMembership.role_code == "owner").limit(1))
                if member is None:
                    raise RuntimeError("No active owner is available for the read-only check")
                actor = Actor(user_id=member.user_id, tenant_id=member.tenant_id, role_code=member.role_code)
                for current, function in checks:
                    function(actor=actor, db=db)
                    print(f"Consulta {current}: OK")
            connection.rollback()
    except Exception as error:
        print(f"Comprobación fallida en {current}: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
