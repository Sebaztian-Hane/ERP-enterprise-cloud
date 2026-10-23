create table public.inventory_adjustment_requests (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  product_id uuid not null references public.products(id),
  warehouse_id uuid not null references public.warehouses(id),
  quantity numeric(14,3) not null check (quantity <> 0),
  reference varchar(100),
  reason varchar(240) not null,
  note text,
  status varchar(16) not null default 'submitted' check (status in ('submitted','approved','rejected','executed','cancelled')),
  approval_request_id uuid unique references public.approval_requests(id),
  requested_by uuid not null references auth.users(id),
  approved_by uuid references auth.users(id),
  executed_by uuid references auth.users(id),
  executed_at timestamptz,
  inventory_movement_id uuid unique references public.inventory_movements(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index ix_inventory_adjustment_requests_tenant_status on public.inventory_adjustment_requests(tenant_id,status,created_at desc);
alter table public.inventory_adjustment_requests enable row level security;
create policy inventory_adjustment_requests_tenant on public.inventory_adjustment_requests for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
