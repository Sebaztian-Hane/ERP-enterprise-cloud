-- Separate the SaaS tenant from the taxpayer and its operational branches.
-- Existing tenants continue to work; configuration can create one or more
-- legal entities before CPE numbering is migrated to this scope.

create table if not exists public.legal_entities (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  code varchar(24) not null,
  legal_name varchar(200) not null,
  trade_name varchar(200),
  tax_id varchar(16) not null,
  tax_regime varchar(80),
  fiscal_address text not null,
  ubigeo varchar(6),
  is_default boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, code),
  unique (tenant_id, tax_id)
);
create unique index if not exists ux_legal_entities_one_default
  on public.legal_entities(tenant_id) where is_default;
create index if not exists ix_legal_entities_tenant_active
  on public.legal_entities(tenant_id, is_active);

create table if not exists public.branches (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  legal_entity_id uuid not null references public.legal_entities(id),
  code varchar(24) not null,
  name varchar(160) not null,
  address text,
  ubigeo varchar(6),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, legal_entity_id, code)
);
create index if not exists ix_branches_tenant_entity_active
  on public.branches(tenant_id, legal_entity_id, is_active);

alter table public.warehouses add column if not exists branch_id uuid references public.branches(id);
create index if not exists ix_warehouses_tenant_branch on public.warehouses(tenant_id, branch_id);

alter table public.legal_entities enable row level security;
alter table public.branches enable row level security;
create policy legal_entities_tenant on public.legal_entities for all to authenticated
  using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy branches_tenant on public.branches for all to authenticated
  using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
