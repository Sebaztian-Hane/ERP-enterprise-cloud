create table public.sales_deliveries (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
 sales_order_id uuid not null references public.sales_orders(id), number varchar(40) not null,
 warehouse_id uuid not null references public.warehouses(id), status varchar(16) not null default 'draft' check(status in ('draft','dispatched','delivered','cancelled')),
 recipient_name varchar(160), recipient_document varchar(16), note text,
 dispatched_by uuid references auth.users(id), dispatched_at timestamptz, delivered_by uuid references auth.users(id), delivered_at timestamptz,
 created_by uuid not null references auth.users(id), created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(tenant_id,number)
);
create table public.sales_delivery_items (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
 delivery_id uuid not null references public.sales_deliveries(id) on delete cascade, product_id uuid not null references public.products(id),
 quantity numeric(14,3) not null check(quantity>0), unique(tenant_id,delivery_id,product_id)
);
create index ix_sales_deliveries_tenant_order on public.sales_deliveries(tenant_id,sales_order_id,status);
alter table public.sales_deliveries enable row level security; alter table public.sales_delivery_items enable row level security;
create policy sales_deliveries_tenant on public.sales_deliveries for all to authenticated using(public.is_active_member(tenant_id)) with check(public.is_active_member(tenant_id));
create policy sales_delivery_items_tenant on public.sales_delivery_items for all to authenticated using(public.is_active_member(tenant_id)) with check(public.is_active_member(tenant_id));
