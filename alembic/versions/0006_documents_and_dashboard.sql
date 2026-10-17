create table public.documents (
  id uuid primary key default gen_random_uuid(), tenant_id uuid not null references public.tenants(id),
  original_filename varchar(255) not null, storage_path varchar(512) not null, content_type varchar(100) not null,
  size_bytes bigint not null check (size_bytes > 0), entity_type varchar(40), entity_id uuid, uploaded_by uuid not null,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(tenant_id, storage_path)
);
create index ix_documents_tenant_created on public.documents(tenant_id, created_at);
alter table public.documents enable row level security;
create policy documents_by_tenant on public.documents for all to authenticated using (public.is_active_member(tenant_id)) with check (public.is_active_member(tenant_id));

insert into public.permissions(code) values ('documents:read'), ('documents:write'), ('reports:read');
insert into public.role_permissions(role_code, permission_code) values
  ('sales','documents:read'), ('sales','documents:write'),
  ('finance','documents:read'), ('finance','documents:write'), ('finance','reports:read'),
  ('viewer','documents:read'), ('viewer','reports:read');

insert into storage.buckets (id, name, public) values ('erp-documents', 'erp-documents', false);
create policy erp_documents_select on storage.objects for select to authenticated
  using (bucket_id = 'erp-documents' and (storage.foldername(name))[1] = 'tenant' and public.is_active_member(((storage.foldername(name))[2])::uuid));
create policy erp_documents_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'erp-documents' and (storage.foldername(name))[1] = 'tenant' and public.is_active_member(((storage.foldername(name))[2])::uuid));
create policy erp_documents_update on storage.objects for update to authenticated
  using (bucket_id = 'erp-documents' and (storage.foldername(name))[1] = 'tenant' and public.is_active_member(((storage.foldername(name))[2])::uuid))
  with check (bucket_id = 'erp-documents' and (storage.foldername(name))[1] = 'tenant' and public.is_active_member(((storage.foldername(name))[2])::uuid));
create policy erp_documents_delete on storage.objects for delete to authenticated
  using (bucket_id = 'erp-documents' and (storage.foldername(name))[1] = 'tenant' and public.is_active_member(((storage.foldername(name))[2])::uuid));
