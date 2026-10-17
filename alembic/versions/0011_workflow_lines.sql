create table public.sales_quote_items (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), quote_id uuid not null references public.sales_quotes(id) on delete cascade,
 product_id uuid not null references public.products(id), quantity numeric(14,3) not null check(quantity>0), unit_price numeric(14,2) not null check(unit_price>=0), line_total numeric(14,2) not null check(line_total>=0), unique(tenant_id,quote_id,product_id));
create table public.sales_order_items (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), order_id uuid not null references public.sales_orders(id) on delete cascade,
 product_id uuid not null references public.products(id), quantity numeric(14,3) not null check(quantity>0), unit_price numeric(14,2) not null check(unit_price>=0), line_total numeric(14,2) not null check(line_total>=0), unique(tenant_id,order_id,product_id));
create table public.purchase_order_items (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), purchase_order_id uuid not null references public.purchase_orders(id) on delete cascade,
 product_id uuid not null references public.products(id), quantity numeric(14,3) not null check(quantity>0), unit_cost numeric(14,2) not null check(unit_cost>=0), line_total numeric(14,2) not null check(line_total>=0), unique(tenant_id,purchase_order_id,product_id));
alter table public.sales_quote_items enable row level security; alter table public.sales_order_items enable row level security; alter table public.purchase_order_items enable row level security;
create policy quote_items_tenant on public.sales_quote_items for all to authenticated using(public.is_active_member(tenant_id)) with check(public.is_active_member(tenant_id));
create policy sales_order_items_tenant on public.sales_order_items for all to authenticated using(public.is_active_member(tenant_id)) with check(public.is_active_member(tenant_id));
create policy purchase_order_items_tenant on public.purchase_order_items for all to authenticated using(public.is_active_member(tenant_id)) with check(public.is_active_member(tenant_id));
