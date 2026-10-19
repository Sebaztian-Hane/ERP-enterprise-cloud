-- Operational foundation: inventory controls, approvals, finance aging,
-- accounting and tenant-level master data.  All records remain tenant scoped.

alter table public.products add column if not exists tracking_mode varchar(12) not null default 'none'
  check (tracking_mode in ('none','lot','serial'));
alter table public.products add column if not exists cost_method varchar(20) not null default 'weighted_average'
  check (cost_method in ('weighted_average','fifo'));
alter table public.products add column if not exists average_cost numeric(14,2) not null default 0 check (average_cost >= 0);

alter table public.accounts_receivable add column if not exists due_date date;
alter table public.accounts_payable add column if not exists due_date date;
update public.accounts_receivable set due_date = created_at::date where due_date is null;
update public.accounts_payable set due_date = created_at::date where due_date is null;
alter table public.accounts_receivable alter column due_date set not null;
alter table public.accounts_payable alter column due_date set not null;
create index if not exists ix_ar_tenant_due on public.accounts_receivable(tenant_id, status, due_date);
create index if not exists ix_ap_tenant_due on public.accounts_payable(tenant_id, status, due_date);

create table public.stock_reservations (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  product_id uuid not null references public.products(id), warehouse_id uuid not null references public.warehouses(id),
  reference_type varchar(32) not null, reference_id uuid, reference varchar(100) not null,
  quantity numeric(14,3) not null check (quantity > 0 and quantity = trunc(quantity)),
  status varchar(16) not null default 'active' check (status in ('active','released','fulfilled','cancelled')),
  note text, created_by uuid not null, released_at timestamptz, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index ix_stock_reservations_available on public.stock_reservations(tenant_id, product_id, warehouse_id, status);

create table public.inventory_counts (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  number varchar(40) not null, warehouse_id uuid not null references public.warehouses(id),
  status varchar(16) not null default 'draft' check (status in ('draft','submitted','approved','applied','cancelled')),
  count_date date not null default current_date, reason varchar(240) not null, note text,
  created_by uuid not null, approved_by uuid, applied_by uuid, applied_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, number)
);
create table public.inventory_count_lines (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  inventory_count_id uuid not null references public.inventory_counts(id) on delete cascade,
  product_id uuid not null references public.products(id), expected_quantity numeric(14,3) not null default 0,
  counted_quantity numeric(14,3) not null check (counted_quantity >= 0 and counted_quantity = trunc(counted_quantity)),
  note text, unique(inventory_count_id, product_id)
);
create index ix_inventory_counts_tenant_status on public.inventory_counts(tenant_id, status, created_at desc);

create table public.approval_policies (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  module varchar(40) not null, action varchar(40) not null, min_amount numeric(14,2) not null default 0 check (min_amount >= 0),
  required_role varchar(32) not null default 'admin', is_active boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, module, action, min_amount)
);
create table public.approval_requests (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  policy_id uuid references public.approval_policies(id), module varchar(40) not null, action varchar(40) not null,
  entity_type varchar(50) not null, entity_id uuid, amount numeric(14,2), status varchar(16) not null default 'pending' check (status in ('pending','approved','rejected','cancelled')),
  payload jsonb not null default '{}'::jsonb, requested_by uuid not null, decided_by uuid, decision_note text, decided_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index ix_approval_requests_tenant_status on public.approval_requests(tenant_id, status, created_at desc);

create table public.chart_of_accounts (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  code varchar(24) not null, name varchar(160) not null, account_type varchar(16) not null check (account_type in ('asset','liability','equity','income','expense')),
  is_postable boolean not null default true, is_active boolean not null default true, created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, code)
);
create table public.accounting_periods (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  name varchar(80) not null, start_date date not null, end_date date not null, status varchar(16) not null default 'open' check (status in ('open','closed')), closed_by uuid, closed_at timestamptz,
  created_at timestamptz not null default now(), unique(tenant_id, start_date), check (end_date >= start_date)
);
create table public.journal_entries (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  number varchar(40) not null, entry_date date not null default current_date, description text not null,
  source_type varchar(40), source_id uuid, status varchar(16) not null default 'posted' check (status in ('draft','posted','voided')),
  created_by uuid not null, created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, number), unique(tenant_id, source_type, source_id)
);
create table public.journal_lines (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  journal_entry_id uuid not null references public.journal_entries(id) on delete cascade, account_id uuid not null references public.chart_of_accounts(id),
  description text, debit numeric(14,2) not null default 0 check (debit >= 0), credit numeric(14,2) not null default 0 check (credit >= 0),
  cost_center_id uuid, check ((debit > 0 and credit = 0) or (credit > 0 and debit = 0))
);
create index ix_journal_entries_tenant_date on public.journal_entries(tenant_id, entry_date desc);

create table public.tax_rates (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), code varchar(24) not null, name varchar(120) not null,
  rate numeric(7,4) not null check (rate >= 0 and rate <= 100), is_default boolean not null default false, is_active boolean not null default true, unique(tenant_id, code)
);
create table public.currencies (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), code varchar(3) not null, name varchar(80) not null, symbol varchar(8), is_base boolean not null default false, is_active boolean not null default true, unique(tenant_id, code)
);
create table public.units_of_measure (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), code varchar(16) not null, name varchar(80) not null, is_active boolean not null default true, unique(tenant_id, code)
);
create table public.payment_terms (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), code varchar(24) not null, name varchar(120) not null, days_due integer not null default 0 check (days_due >= 0), is_active boolean not null default true, unique(tenant_id, code)
);
create table public.cost_centers (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), code varchar(32) not null, name varchar(120) not null, is_active boolean not null default true, unique(tenant_id, code)
);
create table public.document_sequences (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), module varchar(40) not null, document_type varchar(40) not null, prefix varchar(16), next_number bigint not null default 1 check (next_number > 0), is_active boolean not null default true, unique(tenant_id, module, document_type)
);

-- The service API is the only write path. RLS remains enabled to protect all
-- browser-facing direct data access and follows the existing member policy.
do $$ declare t text; begin
  foreach t in array array['stock_reservations','inventory_counts','inventory_count_lines','approval_policies','approval_requests','chart_of_accounts','accounting_periods','journal_entries','journal_lines','tax_rates','currencies','units_of_measure','payment_terms','cost_centers','document_sequences'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('create policy %I on public.%I for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id))', t || '_tenant', t);
  end loop;
end $$;

insert into public.permissions(code) values
 ('approvals:read'),('approvals:write'),('accounting:read'),('accounting:write'),('configuration:read'),('configuration:write') on conflict do nothing;
insert into public.role_permissions(role_code, permission_code) values
 ('owner','approvals:read'),('owner','approvals:write'),('owner','accounting:read'),('owner','accounting:write'),('owner','configuration:read'),('owner','configuration:write'),
 ('admin','approvals:read'),('admin','approvals:write'),('admin','accounting:read'),('admin','accounting:write'),('admin','configuration:read'),('admin','configuration:write'),
 ('finance','accounting:read'),('finance','accounting:write'),('finance','approvals:read'),('viewer','accounting:read') on conflict do nothing;
