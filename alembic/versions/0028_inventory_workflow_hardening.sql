-- Inventory workflow hardening.
--
-- This keeps the current tenant_id multitenancy model and strengthens the
-- operational state kept by the API.  It deliberately does not implement
-- fiscal emission: that responsibility remains behind the external CPE
-- provider adapter.

alter table public.inventory_transfers
  add column if not exists dispatched_by uuid,
  add column if not exists dispatched_at timestamptz,
  add column if not exists received_by uuid,
  add column if not exists received_at timestamptz;

alter table public.inventory_transfers
  drop constraint if exists inventory_transfers_status_check;
alter table public.inventory_transfers
  add constraint inventory_transfers_status_check
  check (status in ('draft', 'in_transit', 'completed', 'cancelled')) not valid;
alter table public.inventory_transfers
  validate constraint inventory_transfers_status_check;

alter table public.inventory_counts
  add column if not exists snapshot_at timestamptz not null default now();

-- One active reservation is the business identity for a product line in a
-- source document.  This makes a retry safe without preventing a new
-- reservation after the original has been released or fulfilled.
create unique index if not exists ux_stock_reservations_active_reference_line
  on public.stock_reservations (
    tenant_id,
    product_id,
    warehouse_id,
    reference_type,
    coalesce(reference_id::text, ''),
    reference
  )
  where status = 'active';

create index if not exists ix_inventory_movements_tenant_warehouse_product_created
  on public.inventory_movements (tenant_id, warehouse_id, product_id, created_at desc);
