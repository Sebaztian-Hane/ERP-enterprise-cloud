create table public.inventory_transfers (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), number varchar(40) not null,
  product_id uuid not null references public.products(id), source_warehouse_id uuid not null references public.warehouses(id), destination_warehouse_id uuid not null references public.warehouses(id),
  quantity numeric(14,3) not null check (quantity > 0), status varchar(16) not null default 'completed' check (status in ('completed','cancelled')),
  note text, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  check (source_warehouse_id <> destination_warehouse_id), unique(tenant_id, number)
);
create index ix_inventory_transfers_tenant_created on public.inventory_transfers(tenant_id, created_at);
alter table public.inventory_transfers enable row level security;
create policy transfers_by_tenant on public.inventory_transfers for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
