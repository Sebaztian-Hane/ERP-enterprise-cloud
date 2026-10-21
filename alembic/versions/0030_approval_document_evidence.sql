-- Immutable operational evidence for approval workflows.
--
-- A document records the exact business payload presented for approval.  It
-- never stores a private key, certificate file, PIN, or provider secret.  A
-- future qualified-signature provider can sign `content_hash` and append its
-- public certificate metadata to approval_signatures.

create table if not exists public.approval_documents (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  approval_request_id uuid references public.approval_requests(id),
  module varchar(40) not null,
  entity_type varchar(50) not null,
  entity_id uuid not null,
  document_number varchar(80) not null,
  version integer not null default 1 check (version > 0),
  status varchar(20) not null default 'submitted'
    check (status in ('submitted','approved','rejected','cancelled')),
  canonical_payload jsonb not null,
  content_hash char(64) not null check (content_hash ~ '^[0-9a-f]{64}$'),
  requested_by uuid not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(tenant_id, entity_type, entity_id, version)
);

create index if not exists ix_approval_documents_tenant_status_created
  on public.approval_documents(tenant_id, status, created_at desc);
create index if not exists ix_approval_documents_request
  on public.approval_documents(approval_request_id);

create table if not exists public.approval_signatures (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  approval_document_id uuid not null references public.approval_documents(id) on delete cascade,
  approval_request_id uuid references public.approval_requests(id),
  signer_user_id uuid not null,
  signature_type varchar(32) not null
    check (signature_type in ('internal_approval','qualified_digital')),
  decision varchar(16) not null check (decision in ('approved','rejected')),
  signed_hash char(64) not null check (signed_hash ~ '^[0-9a-f]{64}$'),
  certificate_subject varchar(500),
  certificate_issuer varchar(500),
  certificate_serial varchar(300),
  provider_reference varchar(300),
  signed_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create unique index if not exists ux_approval_signatures_one_internal_decision
  on public.approval_signatures(approval_request_id)
  where signature_type = 'internal_approval';
create index if not exists ix_approval_signatures_document_created
  on public.approval_signatures(approval_document_id, created_at);
create index if not exists ix_approval_signatures_tenant
  on public.approval_signatures(tenant_id);
create index if not exists ix_approval_signatures_request
  on public.approval_signatures(approval_request_id)
  where approval_request_id is not null;

alter table public.approval_documents enable row level security;
alter table public.approval_signatures enable row level security;
create policy approval_documents_tenant on public.approval_documents for all to authenticated
  using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
create policy approval_signatures_tenant on public.approval_signatures for all to authenticated
  using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));
