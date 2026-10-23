-- Petty cash is a controlled, tenant-owned operational fund.  The source
-- treasury account and its cash account make every allocation/replenishment
-- traceable without treating an expense as an unstructured cash movement.
create table public.petty_cash_funds (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  code varchar(40) not null,
  name varchar(120) not null,
  cash_account_id uuid not null references public.cash_accounts(id),
  funding_cash_account_id uuid not null references public.cash_accounts(id),
  custodian_user_id uuid not null references auth.users(id),
  fixed_amount numeric(14,2) not null check (fixed_amount > 0),
  current_balance numeric(14,2) not null check (current_balance >= 0),
  currency varchar(3) not null,
  status varchar(16) not null default 'active' check (status in ('active','suspended','closed')),
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, code),
  unique(tenant_id, cash_account_id),
  check (cash_account_id <> funding_cash_account_id)
);

create table public.petty_cash_expenses (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  fund_id uuid not null references public.petty_cash_funds(id),
  number varchar(40) not null,
  expense_date date not null default current_date,
  description varchar(500) not null,
  merchant_name varchar(160),
  supplier_tax_id varchar(16),
  receipt_number varchar(100),
  amount numeric(14,2) not null check (amount > 0),
  expense_account_code varchar(24) not null,
  cost_center_id uuid references public.cost_centers(id),
  document_id uuid references public.documents(id),
  status varchar(16) not null default 'draft' check (status in ('draft','submitted','approved','rejected','posted','cancelled')),
  approval_request_id uuid unique references public.approval_requests(id),
  created_by uuid not null references auth.users(id),
  approved_by uuid references auth.users(id),
  posted_by uuid references auth.users(id),
  posted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, number)
);

create table public.petty_cash_replenishments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  fund_id uuid not null references public.petty_cash_funds(id),
  number varchar(40) not null,
  amount numeric(14,2) not null check (amount > 0),
  status varchar(16) not null default 'submitted' check (status in ('submitted','approved','rejected','executed','cancelled')),
  approval_request_id uuid unique references public.approval_requests(id),
  requested_by uuid not null references auth.users(id),
  approved_by uuid references auth.users(id),
  executed_by uuid references auth.users(id),
  executed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, number)
);

create index ix_petty_cash_funds_tenant_status on public.petty_cash_funds(tenant_id, status);
create index ix_petty_cash_expenses_tenant_fund_status on public.petty_cash_expenses(tenant_id, fund_id, status, created_at desc);
create index ix_petty_cash_replenishments_tenant_fund_status on public.petty_cash_replenishments(tenant_id, fund_id, status, created_at desc);

alter table public.cash_movements add column if not exists petty_cash_fund_id uuid references public.petty_cash_funds(id);
alter table public.cash_movements add column if not exists petty_cash_expense_id uuid references public.petty_cash_expenses(id);
alter table public.cash_movements add column if not exists petty_cash_replenishment_id uuid references public.petty_cash_replenishments(id);

alter table public.petty_cash_funds enable row level security;
alter table public.petty_cash_expenses enable row level security;
alter table public.petty_cash_replenishments enable row level security;
create policy petty_cash_funds_tenant on public.petty_cash_funds for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy petty_cash_expenses_tenant on public.petty_cash_expenses for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy petty_cash_replenishments_tenant on public.petty_cash_replenishments for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
