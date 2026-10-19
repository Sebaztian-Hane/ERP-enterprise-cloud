-- EnterpriseCloud maneja unidades enteras; no se permiten fracciones en flujos de inventario.
alter table public.inventories add constraint inventories_quantity_whole check (quantity = trunc(quantity)) not valid;
alter table public.inventory_movements add constraint inventory_movements_quantity_whole check (quantity = trunc(quantity)) not valid;
alter table public.inventory_transfers add constraint inventory_transfers_quantity_whole check (quantity = trunc(quantity)) not valid;
alter table public.sale_items add constraint sale_items_quantity_whole check (quantity = trunc(quantity)) not valid;
alter table public.purchase_items add constraint purchase_items_quantity_whole check (quantity = trunc(quantity)) not valid;
alter table public.sales_quote_items add constraint sales_quote_items_quantity_whole check (quantity = trunc(quantity)) not valid;
alter table public.sales_order_items add constraint sales_order_items_quantity_whole check (quantity = trunc(quantity)) not valid;
alter table public.purchase_order_items add constraint purchase_order_items_quantity_whole check (quantity = trunc(quantity)) not valid;

alter table public.inventories validate constraint inventories_quantity_whole;
alter table public.inventory_movements validate constraint inventory_movements_quantity_whole;
alter table public.inventory_transfers validate constraint inventory_transfers_quantity_whole;
alter table public.sale_items validate constraint sale_items_quantity_whole;
alter table public.purchase_items validate constraint purchase_items_quantity_whole;
alter table public.sales_quote_items validate constraint sales_quote_items_quantity_whole;
alter table public.sales_order_items validate constraint sales_order_items_quantity_whole;
alter table public.purchase_order_items validate constraint purchase_order_items_quantity_whole;
