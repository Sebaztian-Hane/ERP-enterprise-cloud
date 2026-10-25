-- Controlled full returns complete the operational commercial cycle.
-- Partial fiscal returns remain behind a future credit-note CPE adapter.

alter table public.sales drop constraint if exists sales_status_check;
alter table public.sales add constraint sales_status_check
  check (status in ('draft','posted','cancelled','returned'));

alter table public.purchases drop constraint if exists purchases_status_check;
alter table public.purchases add constraint purchases_status_check
  check (status in ('draft','received','cancelled','returned'));

create table public.sales_returns (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  sale_id uuid not null references public.sales(id),
  number varchar(40) not null,
  reason varchar(500) not null,
  status varchar(16) not null default 'posted' check (status in ('posted','cancelled')),
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  unique(tenant_id, sale_id),
  unique(tenant_id, number)
);

create table public.purchase_returns (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  purchase_id uuid not null references public.purchases(id),
  number varchar(40) not null,
  reason varchar(500) not null,
  status varchar(16) not null default 'posted' check (status in ('posted','cancelled')),
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  unique(tenant_id, purchase_id),
  unique(tenant_id, number)
);

create index ix_sales_returns_tenant_created on public.sales_returns(tenant_id, created_at desc);
create index ix_purchase_returns_tenant_created on public.purchase_returns(tenant_id, created_at desc);

alter table public.sales_returns enable row level security;
alter table public.purchase_returns enable row level security;
create policy sales_returns_tenant on public.sales_returns for all to authenticated
  using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy purchase_returns_tenant on public.purchase_returns for all to authenticated
  using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
