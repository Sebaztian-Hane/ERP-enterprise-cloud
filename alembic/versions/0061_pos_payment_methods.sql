-- Every POS tender resolves to a cash/bank account owned by the same tenant.
-- The API validates the cross-table tenant match before insert/update; RLS
-- provides defense in depth for direct authenticated Data API access.
create table public.pos_payment_method_configs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  method varchar(32) not null check (method in ('cash', 'visa', 'mastercard', 'yape', 'plin')),
  cash_account_id uuid not null references public.cash_accounts(id),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, method)
);

create index ix_pos_payment_methods_tenant_active
  on public.pos_payment_method_configs(tenant_id, is_active);

alter table public.pos_payment_method_configs enable row level security;

create policy pos_payment_method_configs_tenant
  on public.pos_payment_method_configs
  for all to authenticated
  using (public.is_active_member(tenant_id))
  with check (public.is_active_member(tenant_id));
