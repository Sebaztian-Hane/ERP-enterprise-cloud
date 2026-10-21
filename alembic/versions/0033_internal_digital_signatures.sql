-- Browser-held, non-exportable signing keys for internal digital signatures.
-- This is deliberately not a qualified certificate workflow. Private keys never
-- reach the API or database; only a public JWK is retained for verification.

create table if not exists public.user_signing_keys (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  user_id uuid not null,
  label varchar(120) not null default 'Este dispositivo',
  algorithm varchar(40) not null default 'ECDSA_P256_SHA256',
  public_jwk jsonb not null,
  fingerprint char(64) not null check (fingerprint ~ '^[0-9a-f]{64}$'),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  unique(tenant_id, user_id, fingerprint)
);

create index if not exists ix_user_signing_keys_tenant_user_active
  on public.user_signing_keys(tenant_id, user_id, is_active);

create table if not exists public.approval_signature_challenges (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  approval_request_id uuid not null references public.approval_requests(id) on delete cascade,
  approval_document_id uuid not null references public.approval_documents(id) on delete cascade,
  signer_user_id uuid not null,
  decision varchar(16) not null check (decision in ('approved','rejected')),
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists ix_approval_signature_challenges_pending
  on public.approval_signature_challenges(tenant_id, signer_user_id, expires_at)
  where used_at is null;

alter table public.approval_signatures
  add column if not exists signing_key_id uuid references public.user_signing_keys(id),
  add column if not exists signature_algorithm varchar(40),
  add column if not exists signature_value text;

alter table public.user_signing_keys enable row level security;
alter table public.approval_signature_challenges enable row level security;

-- Signing key registration and challenges are mediated by the API, which checks
-- tenant membership and role. Do not make cryptographic verification material a
-- directly writable Data API resource.
create policy user_signing_keys_server_only on public.user_signing_keys for all to authenticated using (false) with check (false);
create policy approval_signature_challenges_server_only on public.approval_signature_challenges for all to authenticated using (false) with check (false);
