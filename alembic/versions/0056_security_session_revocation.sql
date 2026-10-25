-- Server-side session revocation.  Supabase JWTs are otherwise valid until
-- their exp claim; this table gives the ERP an immediate invalidation point
-- after password changes and explicit session-revoke requests.

create table if not exists public.user_security_states (
  user_id uuid primary key,
  valid_after_iat bigint not null default 0 check (valid_after_iat >= 0),
  updated_at timestamptz not null default now()
);

alter table public.user_security_states enable row level security;

-- This is server-maintained state.  Clients do not get a direct Data API
-- surface; the authenticated ERP API enforces ownership and writes audits.
create policy user_security_states_server_only
  on public.user_security_states
  for all
  to authenticated
  using (false)
  with check (false);

-- Credentials are also server-only.  An explicit deny policy documents that
-- intention and prevents accidental public access while satisfying RLS audit.
create policy entity_apisunat_credentials_server_only
  on public.entity_apisunat_credentials
  for all
  to authenticated
  using (false)
  with check (false);
