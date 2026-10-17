create table public.bank_statement_lines (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), cash_account_id uuid not null references public.cash_accounts(id),
 transaction_date date not null, description text not null, amount numeric(14,2) not null check(amount<>0), reference varchar(100), reconciled boolean not null default false, cash_movement_id uuid unique references public.cash_movements(id), created_at timestamptz not null default now());
create index ix_bank_statement_tenant_account_date on public.bank_statement_lines(tenant_id,cash_account_id,transaction_date);
alter table public.bank_statement_lines enable row level security;
create policy bank_statement_tenant on public.bank_statement_lines for all to authenticated using(public.is_active_member(tenant_id)) with check(public.is_active_member(tenant_id));
