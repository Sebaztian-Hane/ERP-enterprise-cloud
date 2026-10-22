-- Scope ERP operational folios by legal entity and branch. CPE correlations
-- are intentionally excluded because the configured external provider owns
-- those fiscal sequences.

alter table public.document_sequences
  add column if not exists legal_entity_id uuid references public.legal_entities(id),
  add column if not exists branch_id uuid references public.branches(id);

alter table public.document_sequences
  drop constraint if exists document_sequences_tenant_id_module_document_type_key;

alter table public.document_sequences
  add constraint document_sequences_context_pair_check
  check ((legal_entity_id is null) = (branch_id is null));

alter table public.document_sequences
  add constraint document_sequences_legal_entity_tenant_fkey
  foreign key (legal_entity_id, tenant_id) references public.legal_entities(id, tenant_id),
  add constraint document_sequences_branch_tenant_fkey
  foreign key (branch_id, tenant_id) references public.branches(id, tenant_id);

create unique index if not exists ux_document_sequences_legacy_scope
  on public.document_sequences(tenant_id, module, document_type)
  where legal_entity_id is null and branch_id is null;
create unique index if not exists ux_document_sequences_legal_branch_scope
  on public.document_sequences(tenant_id, legal_entity_id, branch_id, module, document_type)
  where legal_entity_id is not null and branch_id is not null;
