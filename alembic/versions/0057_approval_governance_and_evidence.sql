-- Approval governance: scoped approval matrices, auditable temporary
-- delegations, tamper-evident attachments and internal signature provenance.
-- Certificate-backed signatures remain a separate future integration.

alter table public.approval_policies
  add column if not exists warehouse_id uuid references public.warehouses(id),
  add column if not exists cost_center_id uuid references public.cost_centers(id),
  add column if not exists area_code varchar(40),
  add column if not exists risk_level varchar(16)
    check (risk_level is null or risk_level in ('low','medium','high','critical'));

alter table public.approval_requests
  add column if not exists due_at timestamptz,
  add column if not exists escalated_at timestamptz;
create index if not exists ix_approval_requests_tenant_pending_due
  on public.approval_requests(tenant_id, due_at)
  where status = 'pending' and due_at is not null;

-- The former key did not allow two policies at the same amount for separate
-- warehouses, cost centers, areas or risk levels.
alter table public.approval_policies
  drop constraint if exists approval_policies_tenant_id_module_action_min_amount_key;
create unique index if not exists ux_approval_policies_scoped_threshold
  on public.approval_policies(
    tenant_id, module, action, min_amount,
    coalesce(warehouse_id, '00000000-0000-0000-0000-000000000000'::uuid),
    coalesce(cost_center_id, '00000000-0000-0000-0000-000000000000'::uuid),
    coalesce(area_code, ''), coalesce(risk_level, '')
  );

create table if not exists public.approval_delegations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  delegator_user_id uuid not null references auth.users(id),
  delegate_user_id uuid not null references auth.users(id),
  module varchar(40),
  action varchar(40),
  starts_at timestamptz not null default now(),
  ends_at timestamptz not null,
  status varchar(16) not null default 'active'
    check (status in ('active','revoked','expired')),
  reason varchar(500) not null,
  created_by uuid not null references auth.users(id),
  revoked_by uuid references auth.users(id),
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  check (delegator_user_id <> delegate_user_id),
  check (ends_at > starts_at)
);
create index if not exists ix_approval_delegations_active_delegate
  on public.approval_delegations(tenant_id, delegate_user_id, starts_at, ends_at)
  where status = 'active';

alter table public.documents
  add column if not exists content_hash char(64)
    check (content_hash is null or content_hash ~ '^[0-9a-f]{64}$');

create table if not exists public.approval_document_attachments (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  approval_document_id uuid not null references public.approval_documents(id) on delete restrict,
  document_id uuid not null references public.documents(id) on delete restrict,
  original_filename varchar(255) not null,
  content_type varchar(100) not null,
  size_bytes bigint not null check (size_bytes > 0),
  content_hash char(64) not null check (content_hash ~ '^[0-9a-f]{64}$'),
  attached_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  unique(approval_document_id, document_id)
);
create index if not exists ix_approval_document_attachments_document
  on public.approval_document_attachments(tenant_id, approval_document_id, created_at);

alter table public.approval_signatures
  add column if not exists delegated_from_user_id uuid references auth.users(id),
  add column if not exists signature_challenge_id uuid references public.approval_signature_challenges(id);

-- Evidence payloads and signature records must be append-only at the database
-- layer. The application may only move a document status from submitted to a
-- terminal decision; it cannot alter what was signed.
create or replace function public.prevent_approval_document_tampering()
returns trigger language plpgsql as $$
begin
  if old.tenant_id is distinct from new.tenant_id
     or old.approval_request_id is distinct from new.approval_request_id
     or old.module is distinct from new.module
     or old.entity_type is distinct from new.entity_type
     or old.entity_id is distinct from new.entity_id
     or old.document_number is distinct from new.document_number
     or old.version is distinct from new.version
     or old.canonical_payload is distinct from new.canonical_payload
     or old.content_hash is distinct from new.content_hash
     or old.requested_by is distinct from new.requested_by then
    raise exception 'approval document evidence is immutable';
  end if;
  if old.status <> 'submitted' and new.status is distinct from old.status then
    raise exception 'approval document status is terminal';
  end if;
  return new;
end $$;
drop trigger if exists approval_documents_immutable on public.approval_documents;
create trigger approval_documents_immutable
  before update on public.approval_documents
  for each row execute function public.prevent_approval_document_tampering();

create or replace function public.prevent_approval_signature_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'approval signatures are append-only';
end $$;
drop trigger if exists approval_signatures_append_only on public.approval_signatures;
create trigger approval_signatures_append_only
  before update or delete on public.approval_signatures
  for each row execute function public.prevent_approval_signature_mutation();

alter table public.approval_delegations enable row level security;
alter table public.approval_document_attachments enable row level security;
create policy approval_delegations_server_only on public.approval_delegations
  for all to authenticated using (false) with check (false);
create policy approval_document_attachments_server_only on public.approval_document_attachments
  for all to authenticated using (false) with check (false);
