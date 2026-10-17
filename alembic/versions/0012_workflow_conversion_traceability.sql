alter table public.sales_orders add column quote_id uuid references public.sales_quotes(id);
alter table public.sales add column sales_order_id uuid unique references public.sales_orders(id);
alter table public.purchases add column purchase_order_id uuid unique references public.purchase_orders(id);
alter table public.sales_quotes add column sales_order_id uuid unique references public.sales_orders(id);
