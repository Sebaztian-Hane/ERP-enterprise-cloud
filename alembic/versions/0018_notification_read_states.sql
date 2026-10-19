-- Estado de lectura de alertas dinámicas, aislado por empresa y usuario.
-- La API es el único canal de acceso; no se expone por la Data API de Supabase.
create table if not exists public.notification_read_states (
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  notification_id varchar(160) not null,
  read_at timestamptz not null default now(),
  primary key (tenant_id, user_id, notification_id)
);

create index if not exists ix_notification_read_states_member
  on public.notification_read_states (tenant_id, user_id, read_at desc);

alter table public.notification_read_states enable row level security;

-- This table is intentionally private to the server API.  The browser must
-- never be able to manufacture or inspect a different user's read state.
revoke all on table public.notification_read_states from anon, authenticated;
