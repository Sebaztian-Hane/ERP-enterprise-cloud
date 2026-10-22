-- Persist the warehouse-derived legal context on posted documents.
-- Nullable columns preserve legacy history. The application requires the
-- context for new transactions once a tenant has configured active branches.

alter table public.sales
  add column if not exists legal_entity_id uuid references public.legal_entities(id),
  add column if not exists branch_id uuid references public.branches(id);

alter table public.purchases
  add column if not exists legal_entity_id uuid references public.legal_entities(id),
  add column if not exists branch_id uuid references public.branches(id);

alter table public.electronic_documents
  add column if not exists legal_entity_id uuid references public.legal_entities(id),
  add column if not exists branch_id uuid references public.branches(id);

-- Composite FK constraints prevent a direct Data API write from attaching an
-- object of another tenant. Single-column FKs remain for referential clarity.
alter table public.legal_entities
  add constraint legal_entities_id_tenant_key unique (id, tenant_id);
alter table public.branches
  add constraint branches_id_tenant_key unique (id, tenant_id);

alter table public.warehouses
  add constraint warehouses_branch_tenant_fkey
  foreign key (branch_id, tenant_id) references public.branches(id, tenant_id);

alter table public.sales
  add constraint sales_legal_entity_tenant_fkey
  foreign key (legal_entity_id, tenant_id) references public.legal_entities(id, tenant_id),
  add constraint sales_branch_tenant_fkey
  foreign key (branch_id, tenant_id) references public.branches(id, tenant_id);

alter table public.purchases
  add constraint purchases_legal_entity_tenant_fkey
  foreign key (legal_entity_id, tenant_id) references public.legal_entities(id, tenant_id),
  add constraint purchases_branch_tenant_fkey
  foreign key (branch_id, tenant_id) references public.branches(id, tenant_id);

alter table public.electronic_documents
  add constraint electronic_documents_legal_entity_tenant_fkey
  foreign key (legal_entity_id, tenant_id) references public.legal_entities(id, tenant_id),
  add constraint electronic_documents_branch_tenant_fkey
  foreign key (branch_id, tenant_id) references public.branches(id, tenant_id);

create index if not exists ix_sales_tenant_legal_branch
  on public.sales(tenant_id, legal_entity_id, branch_id);
create index if not exists ix_purchases_tenant_legal_branch
  on public.purchases(tenant_id, legal_entity_id, branch_id);
create index if not exists ix_electronic_documents_tenant_legal_branch
  on public.electronic_documents(tenant_id, legal_entity_id, branch_id);
