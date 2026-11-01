"""Inspect/apply migration 0065 using the private connection; never print secrets."""

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
        with psycopg.connect(url, connect_timeout=10, application_name="erp-enterprise-schema", autocommit=True) as connection:
            with connection.transaction():
                connection.execute("set transaction read only")
                present = connection.execute("select to_regclass('public.enterprise_records') is not null, to_regclass('public.enterprise_record_events') is not null").fetchone()
                create, references = connection.execute("select has_schema_privilege(current_user,'public','CREATE'), has_table_privilege(current_user,'public.tenants','REFERENCES')").fetchone()
            print("Conexión al proyecto ERP: OK")
            print(f"Documentos / historial disponibles: {present[0]}/{present[1]}")
            if args.apply and not all(present):
                if any(present):
                    raise SystemExit("Esquema parcial: revisar antes de ejecutar la migración completa.")
                if not create or not references:
                    raise SystemExit("La conexión de la API no permite migraciones. Ejecuta alembic/versions/0065_enterprise_modules.sql en el editor SQL de Supabase.")
                connection.execute((root / "alembic/versions/0065_enterprise_modules.sql").read_text(encoding="utf-8"), prepare=False)
                print("Migración 0065 aplicada.")
            if all(present) or args.apply:
                rows = connection.execute("select c.relname,c.relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relname in ('enterprise_records','enterprise_record_events') order by c.relname").fetchall()
                print(f"RLS habilitado en ambas tablas: {len(rows) == 2 and all(row[1] for row in rows)}")
                immutable = connection.execute("select exists(select 1 from pg_trigger where tgrelid='public.enterprise_record_events'::regclass and tgname='enterprise_events_immutable' and tgenabled='O')").fetchone()[0]
                print(f"Historial protegido: {immutable}")
                count = connection.execute("select count(*) from public.roles where code in ('hr','maintenance','transport','production','quality','projects','controller','assets','crm','safety','service')").fetchone()[0]
                print(f"Roles especializados disponibles: {count}/11")
    except psycopg.Error as error:
        print(f"Error de conexión o SQL: {type(error).__name__}; SQLSTATE={error.sqlstate or 'sin código'}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
