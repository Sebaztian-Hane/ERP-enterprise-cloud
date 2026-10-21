create table if not exists public.tenant_module_settings (
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  module_code varchar(50) not null,
  is_enabled boolean not null default true,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, module_code),
  constraint tenant_module_settings_code check (module_code in ('sales','purchases','inventory','finance','documents','reports','administration','customers','suppliers','products','quotes','sales_orders','purchase_orders','reconciliation','operations'))
);
create table if not exists public.membership_module_settings (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id) on delete cascade,
  membership_id uuid not null references public.tenant_memberships(id) on delete cascade,
  module_code varchar(50) not null, is_enabled boolean not null,
  updated_at timestamptz not null default now(), unique(membership_id, module_code),
  constraint membership_module_settings_code check (module_code in ('sales','purchases','inventory','finance','documents','reports','administration','customers','suppliers','products','quotes','sales_orders','purchase_orders','reconciliation','operations'))
);
create index if not exists ix_membership_module_settings_tenant_membership on public.membership_module_settings(tenant_id, membership_id);
alter table public.tenant_module_settings enable row level security;
alter table public.membership_module_settings enable row level security;
revoke all on table public.tenant_module_settings from anon, authenticated;
revoke all on table public.membership_module_settings from anon, authenticated;
