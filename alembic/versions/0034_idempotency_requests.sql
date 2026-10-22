-- Durable command idempotency for financial and stock-affecting API requests.
-- A key is scoped to the tenant, authenticated actor and operation.  The body
-- hash prevents a client from accidentally reusing a key for different data.

create table if not exists public.idempotency_requests (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  actor_user_id uuid not null,
  operation varchar(100) not null,
  idempotency_key varchar(200) not null,
  request_hash char(64) not null check (request_hash ~ '^[0-9a-f]{64}$'),
  status varchar(16) not null default 'processing'
    check (status in ('processing', 'completed')),
  response_status smallint,
  response_body jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (tenant_id, actor_user_id, operation, idempotency_key)
);

create index if not exists ix_idempotency_requests_processing
  on public.idempotency_requests (tenant_id, created_at)
  where status = 'processing';

alter table public.idempotency_requests enable row level security;

-- Commands are mediated by the API after membership/permission validation.
-- Do not expose command replay data through the Data API.
create policy idempotency_requests_server_only
  on public.idempotency_requests for all to authenticated
  using (false) with check (false);
