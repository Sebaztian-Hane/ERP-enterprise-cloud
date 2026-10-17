insert into public.permissions(code) values ('tenants:manage');
insert into public.role_permissions(role_code, permission_code) values ('owner','tenants:manage'), ('admin','tenants:manage');
