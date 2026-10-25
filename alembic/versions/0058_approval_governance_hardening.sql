-- Harden the trigger functions introduced by 0057 and cover their foreign-key
-- access paths.  The explicit search_path prevents object-shadowing attacks.

alter function public.prevent_approval_document_tampering() set search_path = public, pg_temp;
alter function public.prevent_approval_signature_mutation() set search_path = public, pg_temp;

create index if not exists ix_approval_delegations_tenant_delegator
  on public.approval_delegations(tenant_id, delegator_user_id, status);
create index if not exists ix_approval_document_attachments_tenant_document
  on public.approval_document_attachments(tenant_id, document_id);
create index if not exists ix_approval_policies_tenant_warehouse
  on public.approval_policies(tenant_id, warehouse_id) where warehouse_id is not null;
create index if not exists ix_approval_policies_tenant_cost_center
  on public.approval_policies(tenant_id, cost_center_id) where cost_center_id is not null;
create index if not exists ix_approval_signatures_challenge
  on public.approval_signatures(signature_challenge_id) where signature_challenge_id is not null;
create index if not exists ix_approval_signatures_delegated_from
  on public.approval_signatures(tenant_id, delegated_from_user_id) where delegated_from_user_id is not null;
