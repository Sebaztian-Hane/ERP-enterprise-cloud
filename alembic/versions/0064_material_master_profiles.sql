-- Additive industrial master. No existing product, stock or document is changed.
begin;

-- Reuse uq_products_tenant_id_id from migration 0040; no new product index.

create table public.product_material_profiles (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references public.tenants(id),
    product_id uuid not null,
    attributes jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint uq_product_material_profile unique (tenant_id, product_id),
    constraint fk_material_product_tenant foreign key (tenant_id, product_id)
        references public.products(tenant_id, id),
    constraint ck_material_attributes_object check (jsonb_typeof(attributes) = 'object')
);
create index ix_product_material_profiles_tenant_id on public.product_material_profiles(tenant_id);
alter table public.product_material_profiles enable row level security;
revoke all on table public.product_material_profiles from anon, authenticated;
create policy product_material_profiles_server_only on public.product_material_profiles
    for all to authenticated using (false) with check (false);

-- The production API uses this existing server role; browser roles stay denied.
do $$ begin
    if exists (select 1 from pg_roles where rolname = 'erp_api') then
        grant select, insert, update, delete on public.product_material_profiles to erp_api;
    end if;
end $$;

commit;
