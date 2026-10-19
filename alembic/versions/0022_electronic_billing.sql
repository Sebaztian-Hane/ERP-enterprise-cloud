-- APISUNAT CPE traceability. Credentials are deliberately not stored here.
create table public.electronic_documents (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  sale_id uuid not null references public.sales(id) on delete restrict,
  document_type varchar(8) not null check (document_type in ('01', '03')),
  series varchar(8) not null,
  correlative varchar(16) not null,
  file_name varchar(80) not null,
  status varchar(24) not null default 'draft'
    check (status in ('draft', 'pending', 'accepted', 'rejected', 'exception', 'void_pending', 'voided', 'error')),
  provider_document_id varchar(80),
  taxable_amount numeric(14,2) not null check (taxable_amount >= 0),
  tax_amount numeric(14,2) not null check (tax_amount >= 0),
  total_amount numeric(14,2) not null check (total_amount >= 0),
  currency varchar(3) not null default 'PEN',
  xml_url text,
  cdr_url text,
  provider_payload jsonb,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, sale_id),
  unique(tenant_id, file_name)
);
create index ix_electronic_documents_tenant_status on public.electronic_documents(tenant_id, status);

alter table public.purchases
  add column supplier_document_type varchar(8),
  add column supplier_document_series varchar(8),
  add column supplier_document_number varchar(16),
  add column supplier_document_date timestamptz,
  add column supplier_tax_amount numeric(14,2) check (supplier_tax_amount >= 0);

alter table public.electronic_documents enable row level security;
create policy electronic_documents_by_tenant on public.electronic_documents
  for all to authenticated
  using (public.is_active_member(tenant_id))
  with check (public.is_active_member(tenant_id));

insert into public.permissions(code) values ('electronic_billing:read'), ('electronic_billing:write')
on conflict do nothing;
insert into public.role_permissions(role_code, permission_code) values
  ('owner', 'electronic_billing:read'), ('owner', 'electronic_billing:write'),
  ('admin', 'electronic_billing:read'), ('admin', 'electronic_billing:write'),
  ('sales', 'electronic_billing:read'), ('sales', 'electronic_billing:write'),
  ('finance', 'electronic_billing:read'), ('finance', 'electronic_billing:write')
on conflict do nothing;
