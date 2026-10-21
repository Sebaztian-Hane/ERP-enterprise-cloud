-- Traceability and valuation for products that explicitly require lots or
-- serial numbers. All stock evidence remains tenant scoped.

alter table public.inventory_movements
  add column if not exists unit_cost numeric(14,2),
  add column if not exists total_cost numeric(14,2);

create table if not exists public.inventory_lots (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  product_id uuid not null references public.products(id),
  warehouse_id uuid not null references public.warehouses(id),
  lot_number varchar(100) not null,
  original_quantity numeric(14,3) not null check (original_quantity > 0 and original_quantity = trunc(original_quantity)),
  available_quantity numeric(14,3) not null check (available_quantity >= 0 and available_quantity = trunc(available_quantity)),
  unit_cost numeric(14,2) not null check (unit_cost >= 0),
  expiration_date date,
  status varchar(16) not null default 'available' check (status in ('available','blocked','exhausted')),
  received_at timestamptz not null default now(),
  created_by uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, product_id, warehouse_id, lot_number)
);

create index if not exists ix_inventory_lots_available
  on public.inventory_lots(tenant_id, product_id, warehouse_id, status, expiration_date, received_at);

create table if not exists public.inventory_serials (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  product_id uuid not null references public.products(id),
  warehouse_id uuid not null references public.warehouses(id),
  lot_id uuid references public.inventory_lots(id),
  serial_number varchar(120) not null,
  unit_cost numeric(14,2) not null check (unit_cost >= 0),
  status varchar(16) not null default 'available' check (status in ('available','reserved','issued','blocked')),
  received_at timestamptz not null default now(),
  issued_at timestamptz,
  created_by uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, product_id, serial_number)
);

create index if not exists ix_inventory_serials_available
  on public.inventory_serials(tenant_id, product_id, warehouse_id, status, received_at);

create table if not exists public.inventory_movement_allocations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  inventory_movement_id uuid not null references public.inventory_movements(id) on delete cascade,
  lot_id uuid references public.inventory_lots(id),
  serial_id uuid references public.inventory_serials(id),
  quantity numeric(14,3) not null check (quantity > 0 and quantity = trunc(quantity)),
  created_at timestamptz not null default now(),
  check ((lot_id is not null) <> (serial_id is not null)),
  unique(inventory_movement_id, lot_id),
  unique(inventory_movement_id, serial_id)
);
create index if not exists ix_inventory_movement_allocations_tenant_movement
  on public.inventory_movement_allocations(tenant_id, inventory_movement_id);

do $$ declare t text; begin
  foreach t in array array['inventory_lots','inventory_serials','inventory_movement_allocations'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('create policy %I on public.%I for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id))', t || '_tenant', t);
  end loop;
end $$;
