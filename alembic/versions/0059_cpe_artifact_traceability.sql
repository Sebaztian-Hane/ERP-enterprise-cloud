-- CPE evidence is append-only. Provider files are copied into the private
-- documents bucket by the API; this database only stores their integrity and
-- traceability metadata.

alter table public.electronic_documents
  drop constraint if exists electronic_documents_status_check;

alter table public.electronic_documents
  add constraint electronic_documents_status_check
  check (status in (
    'draft', 'reserved', 'pending', 'submitted', 'accepted', 'rejected',
    'exception', 'observed', 'unknown', 'void_pending', 'voided', 'error'
  ));

alter table public.electronic_documents
  add column if not exists provider_response_hash char(64),
  add column if not exists provider_response_at timestamptz;

create table if not exists public.electronic_document_artifacts (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  electronic_document_id uuid not null references public.electronic_documents(id) on delete restrict,
  artifact_type varchar(24) not null check (artifact_type in ('provider_response', 'xml', 'cdr', 'pdf')),
  capture_status varchar(16) not null check (capture_status in ('stored', 'unavailable', 'failed')),
  source_url text,
  storage_path varchar(512),
  content_type varchar(120),
  size_bytes bigint check (size_bytes is null or size_bytes >= 0),
  content_hash char(64) check (content_hash is null or content_hash ~ '^[0-9a-f]{64}$'),
  provider_document_id varchar(80),
  error_message text,
  captured_by uuid,
  captured_at timestamptz not null default now(),
  check (
    (capture_status = 'stored' and storage_path is not null and content_hash is not null)
    or (capture_status in ('unavailable', 'failed') and storage_path is null)
  )
);

create index if not exists ix_cpe_artifacts_tenant_document_captured
  on public.electronic_document_artifacts(tenant_id, electronic_document_id, captured_at desc);

create table if not exists public.electronic_document_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  electronic_document_id uuid not null references public.electronic_documents(id) on delete restrict,
  event_type varchar(40) not null,
  document_status varchar(24),
  provider_document_id varchar(80),
  payload_hash char(64) check (payload_hash is null or payload_hash ~ '^[0-9a-f]{64}$'),
  detail text,
  actor_user_id uuid,
  created_at timestamptz not null default now()
);

create index if not exists ix_cpe_events_tenant_document_created
  on public.electronic_document_events(tenant_id, electronic_document_id, created_at desc);

-- Browser clients never access fiscal evidence directly. The API authorizes
-- the tenant and produces a short-lived URL only for an artifact it owns.
alter table public.electronic_document_artifacts enable row level security;
alter table public.electronic_document_events enable row level security;

create policy electronic_document_artifacts_server_only
  on public.electronic_document_artifacts for all to authenticated
  using (false) with check (false);
create policy electronic_document_events_server_only
  on public.electronic_document_events for all to authenticated
  using (false) with check (false);

create or replace function public.prevent_electronic_document_evidence_mutation()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  raise exception 'La evidencia fiscal es inmutable';
end;
$$;

drop trigger if exists electronic_document_artifacts_append_only on public.electronic_document_artifacts;
create trigger electronic_document_artifacts_append_only
before update or delete on public.electronic_document_artifacts
for each row execute function public.prevent_electronic_document_evidence_mutation();

drop trigger if exists electronic_document_events_append_only on public.electronic_document_events;
create trigger electronic_document_events_append_only
before update or delete on public.electronic_document_events
for each row execute function public.prevent_electronic_document_evidence_mutation();
