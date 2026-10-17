create table public.suppliers (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  code varchar(40) not null, name varchar(160) not null, email varchar(254), phone varchar(40), tax_id varchar(40),
  is_active boolean not null default true, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(tenant_id, code)
);
create index ix_suppliers_tenant_name on public.suppliers(tenant_id, name);
create table public.warehouses (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  code varchar(40) not null, name varchar(120) not null, address text, is_active boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, code)
);
create table public.inventories (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  product_id uuid not null references public.products(id), warehouse_id uuid not null references public.warehouses(id),
  quantity numeric(14,3) not null default 0 check (quantity >= 0),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, product_id, warehouse_id)
);
create index ix_inventories_tenant_product on public.inventories(tenant_id, product_id);
create table public.inventory_movements (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  product_id uuid not null references public.products(id), warehouse_id uuid not null references public.warehouses(id),
  movement_type varchar(24) not null check (movement_type in ('adjustment','receipt','issue','transfer_in','transfer_out')),
  quantity numeric(14,3) not null check (quantity <> 0), reference varchar(100), note text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index ix_movements_tenant_product_created on public.inventory_movements(tenant_id, product_id, created_at);

insert into public.permissions(code) values ('suppliers:read'), ('suppliers:write'), ('inventory:read'), ('inventory:write');
insert into public.role_permissions(role_code, permission_code) values
  ('purchases','suppliers:read'), ('purchases','suppliers:write'),
  ('inventory','inventory:read'), ('inventory','inventory:write');

alter table public.suppliers enable row level security;
alter table public.warehouses enable row level security;
alter table public.inventories enable row level security;
alter table public.inventory_movements enable row level security;
create policy suppliers_by_tenant on public.suppliers for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy warehouses_by_tenant on public.warehouses for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy inventories_by_tenant on public.inventories for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy inventory_movements_by_tenant on public.inventory_movements for select to authenticated using (public.is_active_member(tenant_id));
