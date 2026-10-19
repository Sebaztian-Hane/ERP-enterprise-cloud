-- Safe defaults for every existing tenant. New tenant provisioning can reuse
-- these codes and does not depend on a single global currency or tax setup.
insert into public.tax_rates(tenant_id,code,name,rate,is_default,is_active)
select id,'IGV18','IGV 18%',18,true,true from public.tenants
on conflict(tenant_id,code) do nothing;
insert into public.currencies(tenant_id,code,name,symbol,is_base,is_active)
select id,'PEN','Sol peruano','S/',true,true from public.tenants
on conflict(tenant_id,code) do nothing;
insert into public.units_of_measure(tenant_id,code,name,is_active)
select id,'NIU','Unidad',true from public.tenants
on conflict(tenant_id,code) do nothing;
insert into public.payment_terms(tenant_id,code,name,days_due,is_active)
select id,'CONTADO','Pago al contado',0,true from public.tenants
on conflict(tenant_id,code) do nothing;
insert into public.cost_centers(tenant_id,code,name,is_active)
select id,'GENERAL','Operación general',true from public.tenants
on conflict(tenant_id,code) do nothing;
insert into public.document_sequences(tenant_id,module,document_type,prefix,next_number,is_active)
select id,'sales','sale','V',1,true from public.tenants
on conflict(tenant_id,module,document_type) do nothing;
insert into public.document_sequences(tenant_id,module,document_type,prefix,next_number,is_active)
select id,'purchases','purchase','C',1,true from public.tenants
on conflict(tenant_id,module,document_type) do nothing;
