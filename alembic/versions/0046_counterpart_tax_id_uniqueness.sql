-- A DNI/RUC identifies one commercial counterpart inside one tenant.  Empty
-- legacy values remain allowed, but cannot bypass the duplicate guard.

update public.customers set tax_id = null where btrim(coalesce(tax_id, '')) = '';
update public.suppliers set tax_id = null where btrim(coalesce(tax_id, '')) = '';

create unique index uq_customers_tenant_tax_id_present
  on public.customers(tenant_id, tax_id)
  where tax_id is not null;

create unique index uq_suppliers_tenant_tax_id_present
  on public.suppliers(tenant_id, tax_id)
  where tax_id is not null;
