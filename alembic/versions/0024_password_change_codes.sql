-- Password-change challenges are server-only; users never read this table via
-- the Data API. Codes are HMAC hashes, expire quickly and are single-use.
create table if not exists public.password_change_codes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  tenant_id uuid not null references public.tenants(id),
  code_hash varchar(64) not null,
  expires_at timestamptz not null,
  attempts integer not null default 0,
  used_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists ix_password_change_codes_user_created
  on public.password_change_codes(user_id, created_at desc);

alter table public.password_change_codes enable row level security;
revoke all on public.password_change_codes from anon, authenticated;
