-- Persistent assistant history. Conversations are private to one member inside
-- one tenant; the server API is the only supported access path.
create table public.assistant_conversations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  title varchar(160) not null default 'Nueva conversación',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index ix_assistant_conversations_member_updated
  on public.assistant_conversations (tenant_id, user_id, updated_at desc);

create table public.assistant_messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.assistant_conversations(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role varchar(16) not null check (role in ('user', 'assistant')),
  content text not null check (char_length(content) between 1 and 20000),
  created_at timestamptz not null default now()
);

create index ix_assistant_messages_conversation_created
  on public.assistant_messages (conversation_id, created_at);

alter table public.assistant_conversations enable row level security;
alter table public.assistant_messages enable row level security;
revoke all on table public.assistant_conversations, public.assistant_messages from anon, authenticated;

-- Defense in depth: the browser never reads or writes assistant history through
-- Supabase's generated Data API. The FastAPI layer checks tenant + owner first.
create policy "assistant_conversations_server_only"
  on public.assistant_conversations as restrictive for all to authenticated
  using (false) with check (false);
create policy "assistant_messages_server_only"
  on public.assistant_messages as restrictive for all to authenticated
  using (false) with check (false);
