-- Central migration cockpit. Run once in the ERP project's SQL editor.
-- Existing business data is not rewritten. 0065 is required for enterprise objects.
begin;
create table public.migration_projects (
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references public.tenants(id),
 name varchar(120) not null check(length(btrim(name)) >= 3),
 group_codes jsonb not null check(jsonb_typeof(group_codes)='array'),
 object_codes jsonb not null check(jsonb_typeof(object_codes)='array'),
 status varchar(24) not null default 'draft' check(status in ('draft','uploaded','ready','errors','migrated')),
 version integer not null default 1 check(version>0),
 created_by uuid not null,
 simulation jsonb not null default '{}'::jsonb,
 receipt jsonb not null default '{}'::jsonb,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(tenant_id,id)
);
create index ix_migration_projects_tenant_created on public.migration_projects(tenant_id,created_at desc);
create table public.migration_project_objects (
 id uuid primary key default gen_random_uuid(),
 tenant_id uuid not null references public.tenants(id), project_id uuid not null,
 object_code varchar(40) not null, filename varchar(200) not null,
 file_sha256 char(64) not null check(file_sha256 ~ '^[0-9a-f]{64}$'),
 rows jsonb not null check(jsonb_typeof(rows)='array' and jsonb_array_length(rows) between 1 and 2000),
 uploaded_by uuid not null,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 foreign key(tenant_id,project_id) references public.migration_projects(tenant_id,id),
 unique(tenant_id,project_id,object_code)
);
alter table public.migration_projects enable row level security;
alter table public.migration_project_objects enable row level security;
revoke all on public.migration_projects,public.migration_project_objects from public,anon,authenticated;
create policy migration_projects_server_only on public.migration_projects for all to authenticated using(false) with check(false);
create policy migration_objects_server_only on public.migration_project_objects for all to authenticated using(false) with check(false);
do $$ begin
 if exists(select 1 from pg_roles where rolname='erp_api') then
  grant select,insert,update on public.migration_projects to erp_api;
  grant select,insert,update,delete on public.migration_project_objects to erp_api;
 end if;
end $$;
commit;
