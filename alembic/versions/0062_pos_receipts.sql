-- An internal POS receipt is always created even when the buyer does not
-- request a fiscal CPE. It is deliberately not a guía de remisión.
create table public.pos_receipts (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  sale_id uuid not null references public.sales(id),
  ticket_number varchar(40) not null,
  document_type varchar(8) not null check (document_type in ('ticket', '01', '03')),
  customer_identified boolean not null default false,
  tender_total numeric(14,2) not null check (tender_total > 0),
  electronic_document_id uuid references public.electronic_documents(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, sale_id),
  unique(tenant_id, ticket_number)
);

create index ix_pos_receipts_tenant_created
  on public.pos_receipts(tenant_id, created_at desc);

alter table public.pos_receipts enable row level security;
create policy pos_receipts_tenant on public.pos_receipts for all to authenticated
  using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
