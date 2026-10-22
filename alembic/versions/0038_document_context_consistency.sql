-- Keep the branch and legal entity pair internally consistent even if a
-- privileged integration bypasses the application service layer.  This is a
-- regular trigger function (not SECURITY DEFINER), so it does not bypass RLS.

create or replace function public.ensure_document_context_consistency()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
declare
  expected_legal_entity_id uuid;
begin
  if (new.legal_entity_id is null) <> (new.branch_id is null) then
    raise exception 'legal_entity_id and branch_id must be set together';
  end if;

  if new.branch_id is not null then
    select legal_entity_id
      into expected_legal_entity_id
      from public.branches
     where id = new.branch_id
       and tenant_id = new.tenant_id;

    if expected_legal_entity_id is null then
      raise exception 'branch does not belong to the document tenant';
    end if;
    if expected_legal_entity_id <> new.legal_entity_id then
      raise exception 'branch does not belong to the selected legal entity';
    end if;
  end if;
  return new;
end;
$$;

create trigger sales_document_context_consistency
before insert or update of tenant_id, legal_entity_id, branch_id on public.sales
for each row execute function public.ensure_document_context_consistency();

create trigger purchases_document_context_consistency
before insert or update of tenant_id, legal_entity_id, branch_id on public.purchases
for each row execute function public.ensure_document_context_consistency();

create trigger electronic_documents_context_consistency
before insert or update of tenant_id, legal_entity_id, branch_id on public.electronic_documents
for each row execute function public.ensure_document_context_consistency();
