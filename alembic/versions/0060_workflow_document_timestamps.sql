-- Workflow status transitions keep an audit timestamp.  The original
-- pre-transaction document tables predate this convention, so backfill them
-- with their creation timestamp before enforcing the invariant.

alter table public.sales_quotes
  add column if not exists updated_at timestamptz;

alter table public.sales_orders
  add column if not exists updated_at timestamptz;

alter table public.purchase_orders
  add column if not exists updated_at timestamptz;

update public.sales_quotes
set updated_at = created_at
where updated_at is null;

update public.sales_orders
set updated_at = created_at
where updated_at is null;

update public.purchase_orders
set updated_at = created_at
where updated_at is null;

alter table public.sales_quotes
  alter column updated_at set default now(),
  alter column updated_at set not null;

alter table public.sales_orders
  alter column updated_at set default now(),
  alter column updated_at set not null;

alter table public.purchase_orders
  alter column updated_at set default now(),
  alter column updated_at set not null;
