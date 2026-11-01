"""Verify (or explicitly apply) the additive material migration from private .env.

No application startup, mail, outbox worker or business mutation is executed.
Connection strings and secrets are never printed.
"""

import argparse
from pathlib import Path
import sys

from dotenv import dotenv_values
import psycopg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = dotenv_values(root / ".env")
    target = "tufvaozdbbhdiiyrxtlh"
    source = config.get("DATABASE_MIGRATION_URL") if args.apply else config.get("DATABASE_URL")
    url = (source or config.get("DATABASE_URL") or "").replace("postgresql+psycopg://", "postgresql://", 1)
    if target not in url or config.get("SUPABASE_URL", "").rstrip("/") != f"https://{target}.supabase.co":
        raise SystemExit("La configuración no corresponde al proyecto ERP esperado.")
    try:
        with psycopg.connect(url, connect_timeout=10, application_name="erp-material-schema", autocommit=True) as connection:
            with connection.transaction():
                connection.execute("set transaction read only")
                exists, create, references = connection.execute("""
                    select to_regclass('public.product_material_profiles') is not null,
                           has_schema_privilege(current_user, 'public', 'CREATE'),
                           has_table_privilege(current_user, 'public.products', 'REFERENCES')
                             and has_table_privilege(current_user, 'public.tenants', 'REFERENCES')
                """).fetchone()
            print("Conexión al proyecto ERP: OK")
            print(f"Ficha industrial disponible: {exists}")
            print(f"Permisos de creación/referencias: {create}/{references}")
            bypass = connection.execute("select rolbypassrls from pg_roles where rolname=current_user").fetchone()[0]
            print(f"Rol de servidor con BYPASSRLS: {bypass}")
            if args.apply and not exists:
                if not create or not references:
                    raise SystemExit("La cuenta de la API no tiene permisos de migración. Se necesita una conexión de administración de este mismo proyecto.")
                connection.execute((root / "alembic/versions/0064_material_master_profiles.sql").read_text(encoding="utf-8"), prepare=False)
                print("Migración 0064 aplicada.")
            if exists or args.apply:
                enabled, isolated = connection.execute("""
                    select c.relrowsecurity,
                           exists(select 1 from pg_constraint x where x.conrelid=c.oid and x.contype='f' and array_length(x.conkey,1)=2)
                    from pg_class c join pg_namespace n on n.oid=c.relnamespace
                    where n.nspname='public' and c.relname='product_material_profiles'
                """).fetchone()
                print(f"RLS habilitado / FK compuesta: {enabled}/{isolated}")
    except psycopg.Error as error:
        # Do not print connection error details: a driver may include its DSN.
        print(f"Error de conexión o SQL: {type(error).__name__}; SQLSTATE={error.sqlstate or 'sin código'}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
