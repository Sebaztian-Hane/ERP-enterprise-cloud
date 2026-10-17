alter table public.payments add column cash_account_id uuid references public.cash_accounts(id);
