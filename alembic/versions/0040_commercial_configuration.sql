-- Versionable commercial configuration.  These masters deliberately keep
-- their own tenant_id and composite relationships so a configuration row
-- cannot reference a currency, product or price list from another tenant.

-- Composite keys are intentionally redundant with the primary key. They are
-- needed to make PostgreSQL itself enforce the tenant boundary in child rows.
alter table public.currencies add constraint uq_currencies_tenant_id_id unique(tenant_id, id);
alter table public.products add constraint uq_products_tenant_id_id unique(tenant_id, id);

create table public.currency_exchange_rates (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  currency_id uuid not null,
  effective_date date not null,
  rate_to_base numeric(18,6) not null check (rate_to_base > 0),
  source varchar(40) not null default 'manual',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, currency_id, effective_date),
  foreign key(tenant_id, currency_id) references public.currencies(tenant_id, id)
);

create table public.price_lists (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  code varchar(40) not null,
  name varchar(160) not null,
  currency_id uuid not null,
  valid_from date not null default current_date,
  valid_to date,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, code),
  check(valid_to is null or valid_to >= valid_from),
  foreign key(tenant_id, currency_id) references public.currencies(tenant_id, id)
);

alter table public.price_lists add constraint uq_price_lists_tenant_id_id unique(tenant_id, id);

create table public.price_list_items (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  price_list_id uuid not null,
  product_id uuid not null,
  min_quantity numeric(14,3) not null default 0 check (min_quantity >= 0),
  unit_price numeric(14,2) not null check (unit_price >= 0),
  valid_from date not null default current_date,
  valid_to date,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, price_list_id, product_id, min_quantity, valid_from),
  check(valid_to is null or valid_to >= valid_from),
  foreign key(tenant_id, price_list_id) references public.price_lists(tenant_id, id) on delete cascade,
  foreign key(tenant_id, product_id) references public.products(tenant_id, id)
);

create table public.discount_policies (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  code varchar(40) not null,
  name varchar(160) not null,
  scope varchar(24) not null default 'sale' check(scope in ('sale','customer','product','price_list')),
  max_discount_percent numeric(7,4) not null check(max_discount_percent >= 0 and max_discount_percent <= 100),
  requires_approval boolean not null default false,
  valid_from date not null default current_date,
  valid_to date,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, code),
  check(valid_to is null or valid_to >= valid_from)
);

create index ix_currency_exchange_rates_tenant_currency_date
  on public.currency_exchange_rates(tenant_id, currency_id, effective_date desc);
create index ix_price_lists_tenant_active
  on public.price_lists(tenant_id, is_active, valid_from desc);
create index ix_price_list_items_tenant_list_product
  on public.price_list_items(tenant_id, price_list_id, product_id, valid_from desc);
create index ix_discount_policies_tenant_active
  on public.discount_policies(tenant_id, is_active, valid_from desc);

do $$ declare t text; begin
  foreach t in array array['currency_exchange_rates','price_lists','price_list_items','discount_policies'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format(
      'create policy %I on public.%I for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id))',
      t || '_tenant', t
    );
  end loop;
end $$;
