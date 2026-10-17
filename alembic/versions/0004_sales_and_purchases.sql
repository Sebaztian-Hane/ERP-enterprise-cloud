create table public.sales (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  number varchar(40) not null, customer_id uuid not null references public.customers(id), warehouse_id uuid not null references public.warehouses(id),
  status varchar(24) not null default 'posted' check (status in ('draft','posted','cancelled')),
  total numeric(14,2) not null default 0 check (total >= 0), note text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, number)
);
create index ix_sales_tenant_created on public.sales(tenant_id, created_at);
create table public.sale_items (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), sale_id uuid not null references public.sales(id) on delete cascade,
  product_id uuid not null references public.products(id), quantity numeric(14,3) not null check (quantity > 0), unit_price numeric(14,2) not null check (unit_price >= 0),
  line_total numeric(14,2) not null check (line_total >= 0), unique(tenant_id, sale_id, product_id)
);
create index ix_sale_items_tenant on public.sale_items(tenant_id, sale_id);
create table public.purchases (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  number varchar(40) not null, supplier_id uuid not null references public.suppliers(id), warehouse_id uuid not null references public.warehouses(id),
  status varchar(24) not null default 'received' check (status in ('draft','received','cancelled')),
  total numeric(14,2) not null default 0 check (total >= 0), note text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, number)
);
create index ix_purchases_tenant_created on public.purchases(tenant_id, created_at);
create table public.purchase_items (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), purchase_id uuid not null references public.purchases(id) on delete cascade,
  product_id uuid not null references public.products(id), quantity numeric(14,3) not null check (quantity > 0), unit_cost numeric(14,2) not null check (unit_cost >= 0),
  line_total numeric(14,2) not null check (line_total >= 0), unique(tenant_id, purchase_id, product_id)
);
create index ix_purchase_items_tenant on public.purchase_items(tenant_id, purchase_id);

insert into public.permissions(code) values ('sales:read'), ('sales:write'), ('purchases:read'), ('purchases:write');
insert into public.role_permissions(role_code, permission_code) values
  ('sales','sales:read'), ('sales','sales:write'), ('purchases','purchases:read'), ('purchases','purchases:write');

alter table public.sales enable row level security;
alter table public.sale_items enable row level security;
alter table public.purchases enable row level security;
alter table public.purchase_items enable row level security;
create policy sales_by_tenant on public.sales for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy sale_items_by_tenant on public.sale_items for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy purchases_by_tenant on public.purchases for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy purchase_items_by_tenant on public.purchase_items for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
