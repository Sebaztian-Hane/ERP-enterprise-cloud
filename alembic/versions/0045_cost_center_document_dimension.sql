-- Carry an immutable, tenant-scoped cost-center dimension through commercial
-- documents and their accounting entries.  Historical data is assigned to the
-- tenant's GENERAL center instead of being left dimensionless.

insert into public.cost_centers(tenant_id, code, name, is_active)
select id, 'GENERAL', 'Operación general', true
from public.tenants
on conflict (tenant_id, code) do nothing;

alter table public.cost_centers
  add constraint uq_cost_centers_tenant_id_id unique (tenant_id, id);

alter table public.sales
  add column cost_center_id uuid,
  add column cost_center_code varchar(32),
  add column cost_center_name varchar(120);

alter table public.purchases
  add column cost_center_id uuid,
  add column cost_center_code varchar(32),
  add column cost_center_name varchar(120);

update public.sales s
set cost_center_id = c.id,
    cost_center_code = c.code,
    cost_center_name = c.name
from public.cost_centers c
where c.tenant_id = s.tenant_id and c.code = 'GENERAL'
  and s.cost_center_id is null;

update public.purchases p
set cost_center_id = c.id,
    cost_center_code = c.code,
    cost_center_name = c.name
from public.cost_centers c
where c.tenant_id = p.tenant_id and c.code = 'GENERAL'
  and p.cost_center_id is null;

update public.journal_lines l
set cost_center_id = c.id
from public.cost_centers c
where c.tenant_id = l.tenant_id and c.code = 'GENERAL'
  and l.cost_center_id is null;

do $$
begin
  if exists (select 1 from public.sales where cost_center_id is null)
     or exists (select 1 from public.purchases where cost_center_id is null)
     or exists (select 1 from public.journal_lines where cost_center_id is null) then
    raise exception 'No se pudo asignar el centro de costo histórico GENERAL';
  end if;
end;
$$;

alter table public.sales
  alter column cost_center_id set not null,
  alter column cost_center_code set not null,
  alter column cost_center_name set not null,
  add constraint fk_sales_tenant_cost_center foreign key (tenant_id, cost_center_id)
    references public.cost_centers(tenant_id, id);

alter table public.purchases
  alter column cost_center_id set not null,
  alter column cost_center_code set not null,
  alter column cost_center_name set not null,
  add constraint fk_purchases_tenant_cost_center foreign key (tenant_id, cost_center_id)
    references public.cost_centers(tenant_id, id);

alter table public.journal_lines
  alter column cost_center_id set not null,
  add constraint fk_journal_lines_tenant_cost_center foreign key (tenant_id, cost_center_id)
    references public.cost_centers(tenant_id, id);

create index ix_cost_centers_tenant_active on public.cost_centers(tenant_id, is_active, code);
create index ix_sales_tenant_cost_center on public.sales(tenant_id, cost_center_id);
create index ix_purchases_tenant_cost_center on public.purchases(tenant_id, cost_center_id);
create index ix_journal_lines_tenant_cost_center on public.journal_lines(tenant_id, cost_center_id);
