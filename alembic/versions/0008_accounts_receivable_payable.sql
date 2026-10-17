create table public.accounts_receivable (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  sale_id uuid not null references public.sales(id), customer_id uuid not null references public.customers(id),
  original_amount numeric(14,2) not null check (original_amount >= 0), balance_amount numeric(14,2) not null check (balance_amount >= 0 and balance_amount <= original_amount),
  status varchar(16) not null default 'open' check (status in ('open','partial','paid','cancelled')),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, sale_id)
);
create index ix_ar_tenant_status on public.accounts_receivable(tenant_id, status);
create table public.accounts_payable (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  purchase_id uuid not null references public.purchases(id), supplier_id uuid not null references public.suppliers(id),
  original_amount numeric(14,2) not null check (original_amount >= 0), balance_amount numeric(14,2) not null check (balance_amount >= 0 and balance_amount <= original_amount),
  status varchar(16) not null default 'open' check (status in ('open','partial','paid','cancelled')),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, purchase_id)
);
create index ix_ap_tenant_status on public.accounts_payable(tenant_id, status);
create table public.payment_allocations (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), payment_id uuid not null references public.payments(id) on delete cascade,
  receivable_id uuid references public.accounts_receivable(id), payable_id uuid references public.accounts_payable(id), amount numeric(14,2) not null check (amount > 0),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  check ((receivable_id is not null and payable_id is null) or (receivable_id is null and payable_id is not null)),
  unique(payment_id, receivable_id), unique(payment_id, payable_id)
);
create index ix_payment_allocations_tenant on public.payment_allocations(tenant_id, payment_id);

insert into public.permissions(code) values ('accounts:read');
insert into public.role_permissions(role_code, permission_code) values ('finance','accounts:read'), ('viewer','accounts:read');
alter table public.accounts_receivable enable row level security;
alter table public.accounts_payable enable row level security;
alter table public.payment_allocations enable row level security;
create policy ar_by_tenant on public.accounts_receivable for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy ap_by_tenant on public.accounts_payable for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy payment_allocations_by_tenant on public.payment_allocations for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
