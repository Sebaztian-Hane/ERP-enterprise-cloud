-- Payment terms are a mutable master. Posted documents therefore retain a
-- snapshot of the term and its calculated due date for a reliable audit trail.
-- Composite foreign keys keep the master relationship inside the same tenant.
alter table public.payment_terms
  add constraint uq_payment_terms_tenant_id_id unique(tenant_id, id);

alter table public.sales
  add column if not exists payment_term_id uuid,
  add column if not exists payment_term_code varchar(24),
  add column if not exists payment_term_name varchar(120),
  add column if not exists payment_term_days_due integer,
  add column if not exists due_date date;

alter table public.purchases
  add column if not exists payment_term_id uuid,
  add column if not exists payment_term_code varchar(24),
  add column if not exists payment_term_name varchar(120),
  add column if not exists payment_term_days_due integer,
  add column if not exists due_date date;

alter table public.sales
  drop constraint if exists fk_sales_tenant_payment_term,
  add constraint fk_sales_tenant_payment_term
    foreign key (tenant_id, payment_term_id)
    references public.payment_terms(tenant_id, id),
  add constraint chk_sales_payment_term_days_due
    check (payment_term_days_due is null or payment_term_days_due >= 0);

alter table public.purchases
  drop constraint if exists fk_purchases_tenant_payment_term,
  add constraint fk_purchases_tenant_payment_term
    foreign key (tenant_id, payment_term_id)
    references public.payment_terms(tenant_id, id),
  add constraint chk_purchases_payment_term_days_due
    check (payment_term_days_due is null or payment_term_days_due >= 0);

create index if not exists ix_sales_tenant_payment_term
  on public.sales(tenant_id, payment_term_id)
  where payment_term_id is not null;

create index if not exists ix_purchases_tenant_payment_term
  on public.purchases(tenant_id, payment_term_id)
  where payment_term_id is not null;
