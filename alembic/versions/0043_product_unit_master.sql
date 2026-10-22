-- Product units become a tenant-scoped master reference.  `products.unit`
-- remains as a compatibility projection for legacy consumers; application
-- writes derive it exclusively from the selected master unit.

alter table public.units_of_measure
  add constraint uq_units_of_measure_tenant_id_id unique (tenant_id, id);

alter table public.products
  add column unit_id uuid,
  add column unit_code varchar(16),
  add column unit_name varchar(80);

-- All existing products used the original free-text default `unit`.  Map that
-- value to the existing Peruvian/SUNAT NIU master record.  Other historic
-- values become tenant-local units rather than being silently discarded.
insert into public.units_of_measure (tenant_id, code, name, is_active)
select distinct
  p.tenant_id,
  case
    when lower(btrim(p.unit)) in ('', 'unit', 'unidad', 'un') then 'NIU'
    else upper(left(btrim(p.unit), 16))
  end,
  case
    when lower(btrim(p.unit)) in ('', 'unit', 'unidad', 'un') then 'Unidad'
    else left(btrim(p.unit), 80)
  end,
  true
from public.products p
on conflict (tenant_id, code) do nothing;

update public.products p
set unit_id = u.id,
    unit_code = u.code,
    unit_name = u.name,
    unit = u.code
from public.units_of_measure u
where u.tenant_id = p.tenant_id
  and u.code = case
    when lower(btrim(p.unit)) in ('', 'unit', 'unidad', 'un') then 'NIU'
    else upper(left(btrim(p.unit), 16))
  end;

do $$
begin
  if exists (select 1 from public.products where unit_id is null or unit_code is null or unit_name is null) then
    raise exception 'No se pudo resolver una unidad maestra para todos los productos existentes';
  end if;
end;
$$;

alter table public.products
  alter column unit_id set not null,
  alter column unit_code set not null,
  alter column unit_name set not null,
  add constraint fk_products_tenant_unit
    foreign key (tenant_id, unit_id)
    references public.units_of_measure (tenant_id, id);

create index ix_products_tenant_unit_id on public.products (tenant_id, unit_id);
