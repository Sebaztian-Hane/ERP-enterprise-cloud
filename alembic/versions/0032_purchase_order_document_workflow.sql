-- Purchase orders must be submitted and decided through the approval workflow.
-- Directly switching a draft to approved would bypass both authorization and
-- the immutable approval document.

alter table public.purchase_orders
  drop constraint if exists purchase_orders_status_check;
alter table public.purchase_orders
  add constraint purchase_orders_status_check
  check (status in ('draft', 'submitted', 'approved', 'cancelled')) not valid;
alter table public.purchase_orders
  validate constraint purchase_orders_status_check;

create index if not exists ix_purchase_orders_tenant_status_created
  on public.purchase_orders(tenant_id, status, created_at desc);
