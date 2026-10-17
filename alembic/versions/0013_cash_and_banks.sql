create table public.cash_accounts (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), code varchar(40) not null, name varchar(120) not null, account_type varchar(16) not null check(account_type in ('cash','bank')), currency varchar(3) not null default 'MXN', is_active boolean not null default true, created_at timestamptz not null default now(), unique(tenant_id,code));
create table public.cash_movements (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), cash_account_id uuid not null references public.cash_accounts(id), payment_id uuid references public.payments(id), movement_type varchar(16) not null check(movement_type in ('inflow','outflow')), amount numeric(14,2) not null check(amount>0), reference varchar(100), note text, created_at timestamptz not null default now());
create index ix_cash_movements_tenant_created on public.cash_movements(tenant_id,created_at);
alter table public.cash_accounts enable row level security; alter table public.cash_movements enable row level security;
create policy cash_accounts_tenant on public.cash_accounts for all to authenticated using(public.is_active_member(tenant_id)) with check(public.is_active_member(tenant_id));
create policy cash_movements_tenant on public.cash_movements for all to authenticated using(public.is_active_member(tenant_id)) with check(public.is_active_member(tenant_id));
insert into public.permissions(code) values ('treasury:read'),('treasury:write');
insert into public.role_permissions(role_code,permission_code) values ('finance','treasury:read'),('finance','treasury:write'),('viewer','treasury:read');
