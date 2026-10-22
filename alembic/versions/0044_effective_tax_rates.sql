-- Effective-dated tax master data and immutable tax snapshots on commercial
-- documents.  Tax is always resolved inside the tenant boundary at posting.

alter table public.tax_rates
  add column valid_from date not null default date '2000-01-01',
  add column valid_to date,
  add constraint tax_rates_valid_range_check check (valid_to is null or valid_to >= valid_from),
  add constraint uq_tax_rates_tenant_id_id unique (tenant_id, id);

insert into public.tax_rates(tenant_id, code, name, rate, is_default, is_active, valid_from)
select id, 'IGV18', 'IGV 18%', 18, true, true, date '2000-01-01'
from public.tenants
on conflict (tenant_id, code) do update
  set valid_from = least(public.tax_rates.valid_from, excluded.valid_from);

alter table public.sales
  add column tax_rate_id uuid,
  add column tax_rate_code varchar(24),
  add column tax_rate_name varchar(120),
  add column tax_rate_percent numeric(7,4),
  add column taxable_amount numeric(14,2),
  add column tax_amount numeric(14,2);

alter table public.purchases
  add column tax_rate_id uuid,
  add column tax_rate_code varchar(24),
  add column tax_rate_name varchar(120),
  add column tax_rate_percent numeric(7,4),
  add column taxable_amount numeric(14,2),
  add column tax_amount numeric(14,2);

update public.sales s
set tax_rate_id = t.id,
    tax_rate_code = t.code,
    tax_rate_name = t.name,
    tax_rate_percent = t.rate,
    taxable_amount = round(s.total / (1 + t.rate / 100), 2),
    tax_amount = s.total - round(s.total / (1 + t.rate / 100), 2)
from public.tax_rates t
where t.tenant_id = s.tenant_id
  and t.is_default = true
  and t.is_active = true
  and t.valid_from <= s.created_at::date
  and (t.valid_to is null or t.valid_to >= s.created_at::date);

update public.purchases p
set tax_rate_id = t.id,
    tax_rate_code = t.code,
    tax_rate_name = t.name,
    tax_rate_percent = t.rate,
    tax_amount = coalesce(p.supplier_tax_amount, p.total - round(p.total / (1 + t.rate / 100), 2)),
    taxable_amount = p.total - coalesce(p.supplier_tax_amount, p.total - round(p.total / (1 + t.rate / 100), 2))
from public.tax_rates t
where t.tenant_id = p.tenant_id
  and t.is_default = true
  and t.is_active = true
  and t.valid_from <= p.created_at::date
  and (t.valid_to is null or t.valid_to >= p.created_at::date);

do $$
begin
  if exists (select 1 from public.sales where tax_rate_id is null or tax_amount is null or taxable_amount is null)
     or exists (select 1 from public.purchases where tax_rate_id is null or tax_amount is null or taxable_amount is null) then
    raise exception 'No se pudo resolver la tasa tributaria histórica de todos los documentos';
  end if;
end;
$$;

alter table public.sales
  alter column tax_rate_id set not null,
  alter column tax_rate_code set not null,
  alter column tax_rate_name set not null,
  alter column tax_rate_percent set not null,
  alter column taxable_amount set not null,
  alter column tax_amount set not null,
  add constraint fk_sales_tenant_tax_rate foreign key (tenant_id, tax_rate_id)
    references public.tax_rates(tenant_id, id);

alter table public.purchases
  alter column tax_rate_id set not null,
  alter column tax_rate_code set not null,
  alter column tax_rate_name set not null,
  alter column tax_rate_percent set not null,
  alter column taxable_amount set not null,
  alter column tax_amount set not null,
  add constraint fk_purchases_tenant_tax_rate foreign key (tenant_id, tax_rate_id)
    references public.tax_rates(tenant_id, id);

create index ix_tax_rates_tenant_effective on public.tax_rates(tenant_id, is_default, is_active, valid_from desc);
create index ix_sales_tenant_tax_rate on public.sales(tenant_id, tax_rate_id);
create index ix_purchases_tenant_tax_rate on public.purchases(tenant_id, tax_rate_id);
