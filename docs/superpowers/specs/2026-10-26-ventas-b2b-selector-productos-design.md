# Diseño: venta B2B y selección paginada

## Intención

La venta administrativa debe conservar la condición de pago para el vencimiento y registrar, por separado, el medio de cobro previsto. La selección de clientes y productos debe escalar mediante búsqueda y paginación, sin tablas desbordadas.

## Decisión

- Mantener `payment_term` para el vencimiento comercial.
- Persistir `payment_method` como referencia comercial de la venta; no crea un cobro ni sustituye Tesorería.
- Usar una ventana modal paginada para productos y clientes, con el área de tabla confinada a su propio desplazamiento.

## Alcance

Métodos B2B: transferencia bancaria, depósito bancario, cheque, tarjeta corporativa, crédito comercial y efectivo. El modal de productos impide duplicar una línea ya elegida.

## No alcance

No se registra un pago real, no se modifica el POS ni se emite un comprobante electrónico durante esta mejora.
