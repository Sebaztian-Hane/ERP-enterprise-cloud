-- Operational documents for HCM / SD / MM / PP / PM / QM / WM / TM / PS /
-- CO / AA / CRM / EHS / CS. No existing stock, payroll or ledger is rewritten.
begin;

alter table public.tenant_module_settings
 drop constraint if exists tenant_module_settings_code,
 add constraint tenant_module_settings_code check (module_code in
 ('sales','pos','purchases','inventory','finance','documents','reports','administration','customers','suppliers','products','quotes','sales_orders','purchase_orders','reconciliation','operations',
  'hcm','maintenance','transport','production','quality','projects','controlling','assets','crm','safety','service'));
alter table public.membership_module_settings
 drop constraint if exists membership_module_settings_code,
 add constraint membership_module_settings_code check (module_code in
 ('sales','pos','purchases','inventory','finance','documents','reports','administration','customers','suppliers','products','quotes','sales_orders','purchase_orders','reconciliation','operations',
  'hcm','maintenance','transport','production','quality','projects','controlling','assets','crm','safety','service'));

insert into public.roles(code, name) values
 ('hr','Recursos humanos'),('maintenance','Mantenimiento'),('transport','Transporte'),
 ('production','Producción'),('quality','Calidad'),('projects','Proyectos'),
 ('controller','Control de gestión'),('assets','Activos fijos'),('crm','Gestión comercial'),
 ('safety','Seguridad y ambiente'),('service','Servicio al cliente')
on conflict(code) do nothing;

insert into public.permissions(code)
select p.module || ':' || a.action
from (values ('hcm'),('maintenance'),('transport'),('production'),('quality'),('projects'),
 ('controlling'),('assets'),('crm'),('safety'),('service')) p(module)
cross join (values ('read'),('write'),('approve')) a(action)
on conflict(code) do nothing;
insert into public.role_permissions(role_code,permission_code)
select r.role, r.module || ':' || a.action
from (values ('hr','hcm'),('maintenance','maintenance'),('transport','transport'),
 ('production','production'),('quality','quality'),('projects','projects'),
 ('controller','controlling'),('assets','assets'),('crm','crm'),('safety','safety'),('service','service')) r(role,module)
cross join (values ('read'),('write'),('approve')) a(action)
on conflict do nothing;
insert into public.role_permissions(role_code,permission_code) values
 ('sales','crm:read'),('sales','crm:write'),('sales','service:read'),
 ('finance','controlling:read'),('finance','controlling:write'),('finance','assets:read'),('finance','assets:write'),
 ('inventory','production:read'),('inventory','maintenance:read'),('inventory','transport:read'),('inventory','quality:read')
on conflict do nothing;
insert into public.role_permissions(role_code,permission_code)
select 'viewer', p.module || ':read' from (values ('maintenance'),('transport'),('production'),('quality'),('projects'),('controlling'),('assets'),('crm'),('service')) p(module)
on conflict do nothing;

create table public.enterprise_records (
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references public.tenants(id),
 module_code varchar(32) not null, resource_code varchar(40) not null,
 number varchar(40) not null check(length(btrim(number)) > 0),
 title varchar(160) not null check(length(btrim(title)) > 1),
 status varchar(24) not null,
 version integer not null default 1 check(version >= 1),
 data jsonb not null check(jsonb_typeof(data) = 'object'),
 derived jsonb not null default '{}'::jsonb check(jsonb_typeof(derived) = 'object'),
 created_by uuid not null, updated_by uuid not null,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(tenant_id,id), unique(tenant_id,resource_code,number)
);
create index ix_enterprise_records_scope on public.enterprise_records(tenant_id,resource_code,status);
create unique index uq_enterprise_attendance_employee_day
 on public.enterprise_records(tenant_id,(data->>'employee_id'),(data->>'work_date'))
 where resource_code='attendance' and status <> 'cancelled';
create unique index uq_enterprise_vehicle_dispatched
 on public.enterprise_records(tenant_id,(data->>'vehicle_id'))
 where resource_code='trips' and status='dispatched';
create unique index uq_enterprise_vehicle_plate
 on public.enterprise_records(tenant_id,upper(data->>'license_plate'))
 where resource_code='vehicles' and status <> 'cancelled';
create unique index uq_enterprise_selected_supplier_quote
 on public.enterprise_records(tenant_id,(data->>'requisition_id'))
 where resource_code='supplier_quotes' and status='selected';

create table public.enterprise_record_events (
 id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
 record_id uuid not null, version integer not null check(version >= 1),
 action varchar(32) not null, from_state varchar(24), to_state varchar(24) not null,
 actor_user_id uuid not null, note text not null,
 content_hash char(64) not null check(content_hash ~ '^[0-9a-f]{64}$'),
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 foreign key(tenant_id,record_id) references public.enterprise_records(tenant_id,id),
 unique(tenant_id,record_id,version)
);
create index ix_enterprise_events_tenant_record on public.enterprise_record_events(tenant_id,record_id);

alter table public.enterprise_records enable row level security;
alter table public.enterprise_record_events enable row level security;
revoke all on public.enterprise_records,public.enterprise_record_events from anon,authenticated;
create policy enterprise_records_server_only on public.enterprise_records for all to authenticated using(false) with check(false);
create policy enterprise_events_server_only on public.enterprise_record_events for all to authenticated using(false) with check(false);

create function public.reject_enterprise_event_mutation() returns trigger language plpgsql as $$
begin raise exception 'El historial empresarial es inmutable'; end;
$$;
revoke all on function public.reject_enterprise_event_mutation() from public,anon,authenticated;
create trigger enterprise_events_immutable before update or delete on public.enterprise_record_events
 for each row execute function public.reject_enterprise_event_mutation();

do $$ begin
 if exists(select 1 from pg_roles where rolname='erp_api') then
  grant select,insert,update on public.enterprise_records to erp_api;
  grant select,insert on public.enterprise_record_events to erp_api;
 end if;
end $$;
commit;
