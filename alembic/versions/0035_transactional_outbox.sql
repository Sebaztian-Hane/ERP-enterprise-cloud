-- Durable events emitted in the same transaction as the business change.
-- Consumers lease rows with FOR UPDATE SKIP LOCKED, so retries remain safe when
-- a worker restarts or more than one worker is configured in the future.

create table if not exists public.outbox_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  topic varchar(100) not null,
  payload jsonb not null,
  status varchar(16) not null default 'pending'
    check (status in ('pending', 'processing', 'delivered', 'dead')),
  attempts integer not null default 0 check (attempts >= 0),
  available_at timestamptz not null default now(),
  locked_at timestamptz,
  delivered_at timestamptz,
  last_error varchar(1000),
  created_at timestamptz not null default now()
);

create index if not exists ix_outbox_events_dispatch
  on public.outbox_events (available_at, created_at)
  where status = 'pending';

create index if not exists ix_outbox_events_tenant_created
  on public.outbox_events (tenant_id, created_at desc);

alter table public.outbox_events enable row level security;

-- Events contain operational context and are dispatched exclusively by the
-- API/worker after its own tenant and integration authorization checks.
create policy outbox_events_server_only
  on public.outbox_events for all to authenticated
  using (false) with check (false);
