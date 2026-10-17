create table public.payments (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  number varchar(40) not null, payment_type varchar(16) not null check (payment_type in ('receipt','payment')),
  customer_id uuid references public.customers(id), supplier_id uuid references public.suppliers(id),
  amount numeric(14,2) not null check (amount > 0), method varchar(32) not null, reference varchar(100), note text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(tenant_id, number),
  check ((payment_type = 'receipt' and customer_id is not null and supplier_id is null) or (payment_type = 'payment' and supplier_id is not null and customer_id is null))
);
create index ix_payments_tenant_created on public.payments(tenant_id, created_at);
insert into public.permissions(code) values ('payments:read'), ('payments:write');
insert into public.role_permissions(role_code, permission_code) values ('finance','payments:read'), ('finance','payments:write');
alter table public.payments enable row level security;
create policy payments_by_tenant on public.payments for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
