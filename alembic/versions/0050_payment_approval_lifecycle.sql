-- Supplier disbursements are proposed before they affect cash or payables.
alter table public.payments
  add column if not exists status varchar(16) not null default 'posted'
    check (status in ('draft','submitted','approved','rejected','posted','cancelled')),
  add column if not exists approval_request_id uuid unique references public.approval_requests(id),
  add column if not exists requested_by uuid references auth.users(id),
  add column if not exists approved_by uuid references auth.users(id),
  add column if not exists executed_by uuid references auth.users(id),
  add column if not exists executed_at timestamptz;

create index if not exists ix_payments_tenant_status on public.payments(tenant_id, status, created_at desc);
