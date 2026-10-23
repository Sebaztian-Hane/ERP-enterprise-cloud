-- Segregation of duties: operations that require approval may require an
-- independent executor as well. Defaults to strict separation for existing
-- and future policies.
alter table public.approval_policies
  add column if not exists require_executor_separation boolean not null default true;

comment on column public.approval_policies.require_executor_separation is
  'When true, the requester and approver cannot execute the approved operation.';
