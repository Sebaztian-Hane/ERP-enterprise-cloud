-- These tables are written and read exclusively through the trusted API
-- connection.  Browser sessions must never get a direct Data API path to
-- credentials, platform-wide entitlements or member overrides.
--
-- The existing REVOKEs keep the tables non-queryable.  Explicit deny policies
-- provide defense in depth and make that intent visible to the database
-- security advisor instead of leaving an RLS-enabled table policy-less.

do $$ declare t text; begin
  foreach t in array array[
    'password_change_codes',
    'tenant_module_settings',
    'membership_module_settings'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on table public.%I from anon, authenticated', t);
    execute format('drop policy if exists %I on public.%I', t || '_server_only', t);
    execute format(
      'create policy %I on public.%I for all to authenticated using (false) with check (false)',
      t || '_server_only', t
    );
  end loop;
end $$;
