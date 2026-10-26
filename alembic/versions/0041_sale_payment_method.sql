-- The commercial payment method documents the intended collection channel.
-- It never represents a payment entry: Treasury remains the source of truth
-- for cash movements and settlement.
alter table public.sales
  add column if not exists payment_method varchar(32);

alter table public.sales
  drop constraint if exists sales_payment_method_check;

alter table public.sales
  add constraint sales_payment_method_check
  check (payment_method is null or payment_method in (
    'bank_transfer',
    'bank_deposit',
    'check',
    'corporate_card',
    'commercial_credit',
    'cash'
  ));
