-- ERP EnterpriseCloud RAG vector store (separate Supabase project)
-- Run this once in the SQL Editor of the NEW Supabase project.
-- gemini-embedding-001 is requested with outputDimensionality 768 in the n8n workflow.
create extension if not exists vector with schema extensions;

create table if not exists public.rag_documents (
  id text primary key,
  tenant_id uuid not null,
  source text not null,
  title text not null,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  content_hash text,
  updated_at timestamptz,
  embedded_at timestamptz not null default now(),
  embedding extensions.vector(768) not null
);

create index if not exists rag_documents_tenant_source_idx
  on public.rag_documents (tenant_id, source);
create index if not exists rag_documents_embedding_hnsw_idx
  on public.rag_documents using hnsw (embedding vector_cosine_ops);

alter table public.rag_documents enable row level security;
-- No browser policy is deliberately created. Only the server-side Supabase service-role
-- credential in n8n may read/write this table.
revoke all on table public.rag_documents from anon, authenticated;

create table if not exists public.rag_chat_messages (
  id bigint generated always as identity primary key,
  tenant_id uuid not null,
  user_id uuid not null,
  conversation_id text not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null check (char_length(content) <= 8000),
  created_at timestamptz not null default now()
);

create index if not exists rag_chat_messages_scope_idx
  on public.rag_chat_messages (tenant_id, user_id, conversation_id, created_at desc);
alter table public.rag_chat_messages enable row level security;
revoke all on table public.rag_chat_messages from anon, authenticated;

create or replace function public.match_rag_documents(
  query_embedding extensions.vector(768),
  match_tenant_id uuid,
  match_count integer default 8,
  match_threshold double precision default 0.55
)
returns table (
  id text,
  title text,
  content text,
  metadata jsonb,
  similarity double precision
)
language sql
stable
set search_path = public, extensions
as $$
  select
    d.id,
    d.title,
    d.content,
    d.metadata,
    1 - (d.embedding <=> query_embedding) as similarity
  from public.rag_documents d
  where d.tenant_id = match_tenant_id
    and 1 - (d.embedding <=> query_embedding) >= match_threshold
  order by d.embedding <=> query_embedding
  limit greatest(1, least(match_count, 20));
$$;

revoke all on function public.match_rag_documents(extensions.vector, uuid, integer, double precision)
  from public, anon, authenticated;
