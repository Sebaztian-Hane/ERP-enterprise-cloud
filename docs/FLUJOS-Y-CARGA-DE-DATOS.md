# Flujos de operación y carga de materiales

Fecha de revisión: 1 de noviembre de 2026. Esta revisión distingue el código
disponible de la validación pendiente en un entorno PostgreSQL de pruebas.

## Ejecución y repositorios

El workspace contiene dos repositorios Git independientes. La interfaz se
inicia desde `../ERP-enterprise-frontend` con `npm ci` y `npm start`, en
http://localhost:5173/. Usa la API existente de Render y Supabase Auth sin un
archivo `.env`. Este modo lee y escribe los datos de la API conectada después
de autenticar al usuario; una prueba de carga debe usar una empresa de pruebas.

Para desarrollar la API se necesita un `.env` privado con la base de pruebas,
Supabase correspondiente y CORS. Instalar dependencias no crea una base ni
replica automáticamente la base existente. No iniciar una API con credenciales
ficticias ni ejecutar migraciones desde el arranque HTTP.

Cada carpeta tiene su `origin` de GitHub. Commit y push se ejecutan por separado.
`render.yaml` configura despliegue por commit del backend; revisar una rama
antes de integrar a `main`. La existencia de un remoto no publica archivos por
sí sola.

## Contratos transversales

- `tenant_id` proviene de la identidad autorizada; cada referencia se resuelve
  dentro de esa empresa. Entidad legal, sucursal y almacén son dimensiones
  adicionales.
- Una mutación de documentos, stock o dinero usa una transacción, auditoría e
  idempotencia donde la ruta ya la ofrece. La carga masiva conserva el contrato
  `Idempotency-Key` y la verificación de segundo factor.
- Maestros describen; movimientos registran cantidades; documentos conservan
  origen; cuentas conservan obligaciones; tesorería liquida; contabilidad y
  conciliación comprueban el resultado.
- Cancelar, devolver o corregir debe conservar el vínculo al documento origen.
  No sustituir movimientos históricos con una edición del catálogo.
- La validación de un archivo comprueba sintaxis, duplicados y referencias;
  no constituye una simulación completa del resultado fiscal/contable de una
  operación. La confirmación revalida contra la base actual.

## Origen, recorrido y criterio de cierre por módulo

| Módulo | Origen y recorrido | Resultado / control de cierre |
| --- | --- | --- |
| Configuración | Unidades, impuestos, moneda, condiciones, dimensiones | Catálogos activos y consistentes para operar |
| Materiales / productos | SKU + nombre + unidad; clasificación y ficha industrial | Maestro disponible; existencias se cargan aparte |
| Clientes | Identidad DNI/RUC y datos comerciales | Contraparte para cotización, venta y cartera |
| Proveedores | Identidad RUC y contacto | Contraparte para orden, recepción y obligación |
| Almacenes | Ubicación y sucursal, cuando corresponda | Ubicación autorizada para los movimientos |
| Cotizaciones | Cliente + líneas + precios → aprobación → pedido | Conversión vinculada y control de duplicados |
| Pedidos de venta | Cotización/pedido → confirmación y reserva → entrega/venta | Entrega e importe ligados al pedido; saldos revisados |
| Ventas / POS | Cliente/mostrador + líneas + almacén → stock y cuenta | Evidencia de venta, cobro y estado del CPE revisados |
| Órdenes de compra | Proveedor + líneas → envío/aprobación → conversión | Compra vinculada sin duplicar recepción |
| Compras | Proveedor + orden cuando corresponda + almacén → recepción | Stock, cuenta por pagar y asiento asociados |
| Inventario | Recepción/venta/ajuste/transferencia + material + almacén | Movimiento con referencia; lotes/series por sus rutas |
| Kardex | Movimientos ordenados por material/almacén | Historial explicable y conciliable con el saldo |
| Inventario físico | Conteo → diferencia → aprobación/aplicación | Diferencia justificada y movimiento trazable |
| Cuentas por cobrar/pagar | Venta/compra → obligación → aplicación de pagos | Saldo, vencimiento y contraparte conciliados |
| Finanzas / tesorería | Obligación + medio/cuenta → aprobación/pago | Pago aplicado y movimiento de caja/banco documentado |
| Conciliación | Extracto/movimientos → identificación de diferencias | Correspondencia bancaria revisada; excepciones abiertas |
| Control operativo | Solicitud/documento → aprobación y ejecución | Responsable, evidencia, segregación y asientos revisados |
| Documentos y firma | Archivo/documento → aprobación/firma interna | Evidencia privada verificable; no equivale a firma cualificada |
| Reportes / dashboard | Datos de los módulos y filtros de empresa/período | Indicadores con origen y criterios reproducibles |
| Administración / perfil | Membresías, roles, módulos y sesión | Acceso autorizado con segundo factor cuando corresponde |
| Asistente | Contexto autorizado → consulta asistida | Respuesta acotada; no ejecuta operaciones financieras |
| Centro de datos | Plantilla → validación → confirmación → comprobación | Altas/exportación controladas con incidencias y hash |

Estos criterios son el diseño de operación y revisión. No certifican que cada
recorrido esté completo en producción. Las rutas existentes de conversión,
recepción, entrega, devolución, pagos y aprobación se conservan.

## Materiales: contrato de plantilla 2.0

El backend es la fuente de definición de columnas. El cliente la consulta en
`GET /api/v1/imports/templates`; el botón Migrar existente y Centro de datos
usan `DataMigrationPanel` y las mismas rutas `preview`/`commit`.

Las seis columnas de la plantilla original siguen siendo válidas: `sku`,
`name`, `category`, `unit`, `sale_price`, `average_cost`. Los valores de SKU y
nombre son obligatorios. Los 19 campos añadidos son opcionales:

- Control: `tracking_mode`, `cost_method`, `is_active`.
- Identificación: `description`, `material_type`, `barcode`, `brand`,
  `manufacturer`, `manufacturer_part_number`.
- Logística: `net_weight`, `gross_weight`, `weight_unit`, `country_of_origin`,
  `hs_code`, `storage_conditions`.
- Planificación: `lead_time_days`, `minimum_order_quantity`, `reorder_point`,
  `safety_stock`.

Los datos industriales se guardan en `product_material_profiles`, una extensión
por empresa/material con FK compuesta. Las consultas anteriores de productos
mantienen sus columnas. El nuevo perfil tiene RLS y acceso directo revocado a
`anon`/`authenticated`; se consulta y modifica por la API autorizada.

`material_type` clasifica; no modifica automáticamente las reglas de stock de
un servicio. Los parámetros de planificación son informativos; no ejecutan
MRP ni generan pedidos. Elegir un método de costo no certifica su valoración.

### Preparación y carga

1. Configurar unidades activas. `unit`, `unidad` y `un` se aceptan como alias de
   `NIU`; los códigos se resuelven sin distinguir mayúsculas dentro del tenant.
2. Descargar Excel o CSV. Excel contiene `Plantilla`, `Instrucciones`,
   `Diccionario`, `Ejemplo`, `Catalogos`. La hoja de carga está vacía; el ejemplo
   está separado y no se importa. Los catálogos corresponden a la empresa.
3. Completar hasta 2000 filas / 5 MB. Usar valores, sin fórmulas; códigos de
   barras y partidas se conservan como texto. Se aceptan CSV UTF-8 con coma,
   punto y coma o tabulador. Los decimales admiten punto o coma como separador.
4. Validar sin guardar. Se detectan encabezados duplicados/desconocidos,
   campos inválidos, duplicados de SKU, precios/costos no finitos o negativos,
   unidades ajenas/inactivas, pesos incoherentes y referencias existentes.
5. Revisar muestra normalizada y descargar todas las incidencias. Corregir y
   volver a validar si hay errores. La vista previa devuelve SHA-256 del archivo.
6. Confirmar con propietario/administrador y segundo factor. El commit compara
   el digest cuando se envía, revalida, conserva idempotencia y guarda todo en
   una transacción. Un error revierte el lote. La auditoría incluye hash y versión.
7. Conservar el comprobante y revisar Productos. Cargar existencias por separado
   con almacén y motivo. El perfil industrial también se edita desde Productos.

Las cargas son **solo altas**. Un SKU existente genera conflicto; reimportar
una exportación no actualiza ni reemplaza materiales existentes. No hay una
reversión automática de lotes ya confirmados. Una corrección posterior debe
evaluar documentos dependientes y dejar auditoría.

### Exportación

`GET /api/v1/imports/products/export?format=xlsx|csv` requiere propietario o
administrador y acceso al módulo. Exporta activos/inactivos exclusivamente de
la empresa autorizada, con los 25 campos, sin datos de otras empresas ni
existencias. Registra auditoría y usa `Cache-Control: no-store`.

Hasta 10000 materiales por exportación interactiva; si se supera, rechaza la
solicitud sin truncar. CSV neutraliza prefijos de fórmula con apóstrofo; usar
Excel si se necesita conservar literalmente un texto que empieza por `=`,
`+`, `-` o `@`. Excel guarda códigos como texto y no genera fórmulas.

## Despliegue coordinado

1. Ejecutar pruebas de ambos repositorios y revisar el diff/ramas.
2. En PostgreSQL de pruebas con las migraciones anteriores aplicadas, ejecutar
   `alembic/versions/0064_material_master_profiles.sql` una sola vez. No ejecutarla
   en producción desde la aplicación ni desde la prueba unitaria.
3. Verificar FK compuesta, RLS y revocaciones con dos empresas; un perfil de A
   no puede referenciar un producto de B ni consultarse por su UUID desde B.
4. Desplegar primero migración y API; comprobar `/health` y `/ready`, descargar
   plantilla, validar/cargar un material de prueba y exportarlo.
5. Desplegar frontend. Confirmar ambos accesos: botón Migrar y Centro de datos.
   La interfaz sigue aceptando el contrato de la API anterior si esta aún no
   anuncia descargas/exportación nuevas.
6. Verificar carga errónea sin filas persistidas, rechazo sin segundo factor,
   reintento idempotente y catálogo de otro tenant sin filtraciones.

Revertir el código de frontend/API conserva los datos industriales en la tabla
adicional. No borrar esa tabla ni eliminar atributos confirmados como parte de
un rollback de aplicación.

## Verificado localmente y pendientes reales

Las pruebas automatizadas cubren el flujo HTTP de materiales con una base
relacional aislada, validación sin escrituras, persistencia/exportación,
rollback, separación entre tenants y permisos/MFA. La compilación y pruebas
del frontend verifican el transporte y reintento idempotente. SQLite no
certifica RLS, bloqueos/concurrencia ni la ejecución de la migración PostgreSQL.

La regresión de navegador se ejecuta con Playwright en un contexto aislado,
con datos de prueba y sin escribir en producción. Cubre los siete roles,
restricciones de empresa, importación, reservas, consulta de materiales y
presentación móvil. Se revisaron capturas de escritorio y móvil. La comprobación
SQL de las consultas operativas también se ejecuta en una transacción de solo
lectura contra la conexión autorizada del backend.

La API puede publicarse antes de la migración 0064: anuncia nueve campos base
mientras no exista la tabla industrial; no admite guardar atributos que aún
no puede persistir. Después de aplicar la migración se habilitan 25 columnas en
la siguiente consulta, sin reinicio. Contabilidad, Aprobaciones, Flujo de caja,
Disponibilidad/reservas, Valoración y Guía usan rutas/esquemas ya existentes.

El acceso efectivo se obtiene de `/auth/access`: se conservan los siete roles
y sus permisos; se intersectan las restricciones por empresa/miembro y perfil
comercial. Una habilitación individual no puede sobrepasar una restricción de
empresa. La guía está en la URL pública con el fragmento `#guia`.

Siguiente revisión por módulo: compra (recepción parcial y tres vías), venta
(pedido–entrega–CPE–cobro), inventario (lotes/series/concurrencia/costeo),
tesorería (conciliación y excepciones), contabilidad (inmutabilidad y cierres),
aprobaciones/documentos y reportes. Ver `PENDIENTES-ERP-ACTUALIZADO.md` para
trabajo todavía abierto; esta entrega no marca esos recorridos como completos.

Referencia de experiencia: [SAP S/4HANA Migration Cockpit](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/29193bf0ebdd4583930b2176cb993268/2f0dbe4111214bcf9b2d57eca26f0525.html).
Se adopta la secuencia de preparación y revisión, con una implementación
propia y los límites funcionales descritos arriba.
