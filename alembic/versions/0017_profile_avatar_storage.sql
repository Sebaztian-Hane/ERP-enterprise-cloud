-- Private avatars: authenticated members can operate only inside their own folder.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'profile-avatars',
  'profile-avatars',
  false,
  3145728,
  array['image/jpeg', 'image/png', 'image/webp']
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create policy "profile avatar owners can read"
on storage.objects for select to authenticated
using (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = (select auth.uid()::text));

create policy "profile avatar owners can upload"
on storage.objects for insert to authenticated
with check (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = (select auth.uid()::text));

create policy "profile avatar owners can update"
on storage.objects for update to authenticated
using (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = (select auth.uid()::text))
with check (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = (select auth.uid()::text));

create policy "profile avatar owners can delete"
on storage.objects for delete to authenticated
using (bucket_id = 'profile-avatars' and (storage.foldername(name))[1] = (select auth.uid()::text));
