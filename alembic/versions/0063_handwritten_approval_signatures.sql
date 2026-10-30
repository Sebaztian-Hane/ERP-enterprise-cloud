-- A visual handwritten trace is evidence accompanying the existing internal
-- cryptographic signature. It does not represent a qualified certificate.
alter table public.approval_signatures
  add column if not exists handwritten_signature_svg text;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'approval_signatures_handwritten_svg_size'
      and conrelid = 'public.approval_signatures'::regclass
  ) then
    alter table public.approval_signatures
      add constraint approval_signatures_handwritten_svg_size
      check (handwritten_signature_svg is null or octet_length(handwritten_signature_svg) <= 60000)
      not valid;
  end if;
end
$$;
