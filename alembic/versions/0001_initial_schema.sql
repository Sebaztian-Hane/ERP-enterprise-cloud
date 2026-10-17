-- Ejecutar con una conexión de administrador de Supabase/PostgreSQL.
-- La API usa conexión de servidor y de todas formas fuerza tenant_id y permisos.
create extension if not exists pgcrypto;

create table public.tenants (
  id uuid primary key default gen_random_uuid(), name varchar(160) not null,
  timezone varchar(64) not null default 'America/Mexico_City', is_active boolean not null default true,
  created_at timestamptz not null default now()
);
create table public.roles (
  code varchar(32) primary key, name varchar(80) not null, description text
);
create table public.permissions (
  code varchar(80) primary key, description text
);
create table public.role_permissions (
  role_code varchar(32) not null references public.roles(code) on delete cascade,
  permission_code varchar(80) not null references public.permissions(code) on delete cascade,
  primary key(role_code, permission_code)
);
insert into public.roles(code, name) values
  ('owner','Propietario'), ('admin','Administrador'), ('sales','Ventas'),
  ('purchases','Compras'), ('inventory','Inventario'), ('finance','Finanzas'), ('viewer','Consulta');
insert into public.permissions(code) values
  ('customers:read'), ('customers:write'), ('products:read'), ('products:write');
insert into public.role_permissions(role_code, permission_code) values
  ('sales','customers:read'), ('sales','customers:write'), ('sales','products:read'),
  ('inventory','products:read'), ('inventory','products:write'),
  ('viewer','customers:read'), ('viewer','products:read');
create table public.tenant_memberships (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  user_id uuid not null references auth.users(id), role_code varchar(32) not null references public.roles(code),
  is_active boolean not null default true, created_at timestamptz not null default now(), unique(tenant_id, user_id)
);
create index ix_memberships_user_active on public.tenant_memberships(user_id, is_active);

create table public.customers (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  code varchar(40) not null, name varchar(160) not null, email varchar(254), phone varchar(40), tax_id varchar(40),
  credit_limit numeric(14,2) not null default 0 check (credit_limit >= 0), is_active boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, code)
);
create index ix_customers_tenant_name on public.customers(tenant_id, name);
create table public.categories (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  name varchar(100) not null, description text, created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, name)
);
create table public.products (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  sku varchar(64) not null, name varchar(160) not null, category_id uuid references public.categories(id),
  unit varchar(16) not null default 'unit', sale_price numeric(14,2) not null default 0 check (sale_price >= 0), is_active boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, sku)
);
create index ix_products_tenant_name on public.products(tenant_id, name);
create table public.audit_logs (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id), actor_user_id uuid not null,
  action varchar(80) not null, module varchar(50) not null, entity varchar(50) not null, entity_id uuid,
  outcome varchar(20) not null default 'success', detail text, created_at timestamptz not null default now()
);
create index ix_audit_logs_tenant_created on public.audit_logs(tenant_id, created_at);

-- RLS de defensa en profundidad para cualquier acceso por Data API.
alter table public.tenants enable row level security;
alter table public.roles enable row level security;
alter table public.permissions enable row level security;
alter table public.role_permissions enable row level security;
alter table public.tenant_memberships enable row level security;
alter table public.customers enable row level security;
alter table public.categories enable row level security;
alter table public.products enable row level security;
alter table public.audit_logs enable row level security;

create function public.is_active_member(requested_tenant uuid) returns boolean
language sql stable security invoker set search_path = public
as $$ select exists (select 1 from public.tenant_memberships m where m.tenant_id = requested_tenant and m.user_id = (select auth.uid()) and m.is_active) $$;

create policy tenant_membership_select on public.tenant_memberships for select to authenticated using (user_id = (select auth.uid()));
create policy tenants_select on public.tenants for select to authenticated using (public.is_active_member(id));
create policy roles_select on public.roles for select to authenticated using (true);
create policy permissions_select on public.permissions for select to authenticated using (true);
create policy role_permissions_select on public.role_permissions for select to authenticated using (true);
create policy customers_by_tenant on public.customers for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy categories_by_tenant on public.categories for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy products_by_tenant on public.products for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy audit_by_tenant on public.audit_logs for select to authenticated using (public.is_active_member(tenant_id));
