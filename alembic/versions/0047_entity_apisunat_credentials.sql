-- APISUNAT credentials are issuer-specific, not installation-wide.
-- The ciphertext is server-only: RLS plus revoked client grants prevent a
-- browser/Data API caller from reading or mutating provider access.
create table if not exists public.entity_apisunat_credentials (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  legal_entity_id uuid not null references public.legal_entities(id) on delete cascade,
  issuer_ruc varchar(11) not null check (issuer_ruc ~ '^[0-9]{11}$'),
  persona_id varchar(160) not null,
  persona_token_ciphertext text not null,
  invoice_series varchar(8) not null default 'F001' check (invoice_series ~ '^[A-Z0-9]{4,8}$'),
  receipt_series varchar(8) not null default 'B001' check (receipt_series ~ '^[A-Z0-9]{4,8}$'),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, legal_entity_id)
);

create index if not exists ix_entity_apisunat_credentials_tenant_entity_active
  on public.entity_apisunat_credentials (tenant_id, legal_entity_id, is_active);

alter table public.entity_apisunat_credentials enable row level security;
revoke all on table public.entity_apisunat_credentials from anon, authenticated;
comment on table public.entity_apisunat_credentials is
  'Server-only encrypted APISUNAT credentials. No client-side RLS policy is intentionally defined.';
