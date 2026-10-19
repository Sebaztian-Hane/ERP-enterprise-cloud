-- Supplier master data: SUNAT RUC lookup needs an address and an ERP tenant
-- must not create two supplier records for the same non-empty RUC.
alter table public.suppliers add column if not exists address text;

create unique index if not exists ux_suppliers_tenant_tax_id
  on public.suppliers (tenant_id, tax_id)
  where tax_id is not null and btrim(tax_id) <> '';
