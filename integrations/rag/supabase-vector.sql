-- ERP EnterpriseCloud RAG store for n8n native LangChain nodes.
-- Gemini Embedding 2 defaults to 3072 dimensions. halfvec permits an HNSW
-- index at this dimension while keeping the standard n8n documents schema.
create extension if not exists vector with schema extensions;

create table if not exists public.documents (
  id bigint generated always as identity primary key,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding extensions.halfvec(3072) not null,
  created_at timestamptz not null default now()
);

create index if not exists documents_embedding_hnsw_idx
  on public.documents using hnsw (embedding extensions.halfvec_cosine_ops);
create index if not exists documents_tenant_idx
  on public.documents ((metadata->>'tenant_id'));

alter table public.documents enable row level security;
revoke all on table public.documents from anon, authenticated;

-- This is the exact function signature used by n8n Supabase Vector Store.
-- A missing tenant filter fails closed, instead of returning another company.
create or replace function public.match_documents(
  query_embedding extensions.halfvec(3072),
  match_count integer default 8,
  filter jsonb default '{}'::jsonb
)
returns table (
  id bigint,
  content text,
  metadata jsonb,
  similarity double precision
)
language plpgsql
stable
set search_path = public, extensions
as $$
begin
  if not (filter ? 'tenant_id') then
    raise exception 'tenant_id filter is required';
  end if;

  return query
  select d.id, d.content, d.metadata, 1 - (d.embedding <=> query_embedding) as similarity
  from public.documents d
  where d.metadata @> filter
  order by d.embedding <=> query_embedding
  limit greatest(1, least(match_count, 20));
end;
$$;

create or replace function public.delete_erp_tenant_documents(p_tenant_id uuid)
returns void
language sql
security invoker
set search_path = public
as $$
  delete from public.documents where metadata->>'tenant_id' = p_tenant_id::text;
$$;

revoke all on function public.match_documents(extensions.halfvec, integer, jsonb)
  from public, anon, authenticated;
revoke all on function public.delete_erp_tenant_documents(uuid)
  from public, anon, authenticated;
