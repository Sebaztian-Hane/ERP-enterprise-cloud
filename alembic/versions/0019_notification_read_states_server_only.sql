-- Política explícita de defensa en profundidad: ni siquiera si se concediera
-- accidentalmente un privilegio futuro, el navegador podrá usar esta tabla.
create policy "notification_read_states_server_only"
  on public.notification_read_states
  as restrictive
  for all
  to authenticated
  using (false)
  with check (false);
