-- Preserve the price-list context used to create a sale. Sale items retain
-- their resolved unit prices, while this link gives the audit trail the
-- commercial policy that produced those prices.
alter table public.sales
  add column if not exists price_list_id uuid;

alter table public.sales
  drop constraint if exists fk_sales_tenant_price_list;

alter table public.sales
  add constraint fk_sales_tenant_price_list
  foreign key (tenant_id, price_list_id)
  references public.price_lists (tenant_id, id);

create index if not exists ix_sales_tenant_price_list
  on public.sales (tenant_id, price_list_id)
  where price_list_id is not null;
