-- A transfer of tracked stock must carry the same evidence as a sale or
-- purchase receipt. Serial numbers are selected before dispatch; lot movement
-- is allocated FEFO from the source warehouse.

alter table public.inventory_transfers
  add column if not exists serial_numbers jsonb;

alter table public.inventory_transfers
  add constraint inventory_transfers_serial_numbers_array
  check (serial_numbers is null or jsonb_typeof(serial_numbers) = 'array');
