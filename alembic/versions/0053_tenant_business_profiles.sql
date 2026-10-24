-- Operational profile controls the default commercial surface for a tenant.
-- It never replaces tenant_id as the isolation boundary.
alter table public.tenants
  add column if not exists business_profile varchar(16) not null default 'hybrid',
  add constraint tenants_business_profile_check
    check (business_profile in ('retail', 'wholesale', 'hybrid'));

alter table public.tenant_module_settings
  drop constraint if exists tenant_module_settings_code,
  add constraint tenant_module_settings_code
    check (module_code in ('sales','pos','purchases','inventory','finance','documents','reports','administration','customers','suppliers','products','quotes','sales_orders','purchase_orders','reconciliation','operations'));

alter table public.membership_module_settings
  drop constraint if exists membership_module_settings_code,
  add constraint membership_module_settings_code
    check (module_code in ('sales','pos','purchases','inventory','finance','documents','reports','administration','customers','suppliers','products','quotes','sales_orders','purchase_orders','reconciliation','operations'));
