-- The event trigger needs SECURITY DEFINER to enable RLS after DDL, but it must
-- never be callable through the public Supabase RPC API.
revoke all on function public.rls_auto_enable() from public, anon, authenticated;
grant execute on function public.rls_auto_enable() to postgres, service_role;
