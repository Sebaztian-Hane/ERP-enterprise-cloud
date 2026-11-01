"""Run all sample imports against a configured database, ALWAYS rolling back.

Usage: python -m scripts.check_migration_simulation --tenant-name "Your test tenant"
No credentials or customer records are printed. No external service is called.
"""
import argparse
import json
from hashlib import sha256
from uuid import uuid4
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.entities import Tenant, TenantMembership
from app.security.auth import Actor
from app.api.routes.migration_projects import CreateProject
from app.services import migration_catalog as catalog, migration_projects as service
from app.services.migration_examples import example_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-name", required=True)
    args = parser.parse_args()
    engine = create_engine(get_settings().database_url)
    with Session(engine) as db:
        try:
            tenant = db.scalars(select(Tenant).where(Tenant.name == args.tenant_name, Tenant.is_active.is_(True))).one()
            member = db.scalars(select(TenantMembership).where(TenantMembership.tenant_id == tenant.id, TenantMembership.role_code == "owner", TenantMembership.is_active.is_(True))).first()
            if not member: raise ValueError("La empresa necesita un propietario activo")
            actor = Actor(user_id=member.user_id, tenant_id=tenant.id, role_code="owner", assurance_level="aal1")
            groups = [group["code"] for group in catalog.groups(catalog.accessible(actor, db))]
            project = service.create_project(db, actor, CreateProject(request_id=uuid4(), name="Comprobación transaccional temporal", groups=groups))
            samples = example_rows(project.id.hex[:8].upper())
            for code in project.object_codes:
                rows = [{key: row.get(key, "") for key in catalog.DEFINITIONS[code]["columns"]} for row in samples[code]]
                digest = sha256(json.dumps(rows).encode()).hexdigest()
                service.stage(db, actor, project, code, rows, code + ".csv", digest, project.version)
            service.simulate(db, actor, project, project.version)
            print(json.dumps(project.simulation, ensure_ascii=False, indent=2))
            return 0 if project.simulation["can_commit"] else 1
        finally:
            db.rollback()
            print("ROLLBACK: no se guardaron grupos, datos, auditorías ni eventos de esta comprobación.")
            engine.dispose()


if __name__ == "__main__": raise SystemExit(main())
