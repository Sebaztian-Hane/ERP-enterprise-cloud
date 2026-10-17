alter table public.products add column minimum_stock numeric(14,3) not null default 0 check(minimum_stock>=0);
