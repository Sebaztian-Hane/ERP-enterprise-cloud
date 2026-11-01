# Centro de migraciones: guía de carga

Aplicación: https://erp-enterprise-frontend.vercel.app/#centro-de-datos

La migración se realiza exclusivamente desde **Centro de migraciones**, en Aplicaciones. El propietario o administrador ve los objetos de los módulos habilitados para su empresa **y** su usuario. Deshabilitar un módulo también bloquea el acceso a los grupos que lo contienen. Los demás roles conservan sus transacciones operativas; no reciben permisos de carga masiva.

## Primera carga, paso a paso

1. Selecciona la empresa correcta. Para practicar utiliza **DUNS - Evaluación ERP**. Configura tu verificación en dos pasos desde Mi perfil antes de confirmar una carga.
2. En Centro de migraciones pulsa **Crear grupo**, escribe un nombre y selecciona los grupos de módulos. Por ejemplo, Maestros comunes + WM para materiales y existencias. Se añaden automáticamente las plantillas predecesoras habilitadas; no se habilitan módulos nuevos ni se cambian roles.
3. En la fila del objeto abre **Acciones → Descargar plantilla Excel**. Completa únicamente la hoja **Plantilla**. Consulta el diccionario o guía incluido. Conserva todos los encabezados y utiliza valores, sin fórmulas.
4. Usa el mismo código de referencia en los archivos relacionados. Ejemplo: `products.sku = DUNSDEMO-PT`, `inventory.sku = DUNSDEMO-PT` y `requisitions.product_sku = DUNSDEMO-PT`. No copies UUID internos.
5. En cada objeto selecciona **Acciones → Cargar archivo**. Puedes subirlos en cualquier orden; el sistema resuelve el orden al simular. También puedes usar **Acciones del grupo → Cargar paquete ZIP**: archivos en la raíz, llamados exactamente `units.xlsx`, `products.xlsx`, etc. No subas plantillas vacías.
6. Pulsa **Simular grupo**. El sistema ejecuta las validaciones, relaciones, stock y reglas contables dentro de una transacción que revierte. Solo conserva archivos preparados y resultados; todavía no crea documentos empresariales.
7. Si aparece **Con incidencias**, consulta **Acciones → Mensajes y dependencias**. Corrige el archivo afectado, vuelve a cargarlo y simula el grupo. Cambiar cualquier archivo invalida la simulación anterior.
8. Con **Simulación correcta**, pulsa **Revisar y confirmar grupo**. Revisa el efecto de cada objeto, marca la casilla **He revisado el grupo y confirmo la carga completa en la empresa seleccionada** y pulsa **Confirmar carga completa**. La API exige segundo factor.
9. La aplicación vuelve a comprobar automáticamente los datos actuales y guarda el grupo en una sola transacción. Si algo falla, revierte el grupo completo. No necesitas repetir manualmente la validación en cada módulo.
10. Descarga el comprobante desde **Acciones del grupo** y revisa los módulos. Un reintento de la misma confirmación devuelve el comprobante sin duplicar documentos. Un grupo migrado queda cerrado para nuevas cargas; crea otro para las siguientes altas.

## Qué plantilla llenar primero

No existe una plantilla universal que deba llenarse para todas las empresas. **Materiales es el maestro compartido** para los procesos que utilizan productos. Para una empresa inicialmente vacía:

| Necesidad | Plantillas que debes preparar | Resultado compartido |
| --- | --- | --- |
| Catálogo de materiales | `units` si falta la unidad → `products` | El SKU y su ficha de 25 campos quedan disponibles en compras, ventas, inventario, producción y calidad. |
| Existencias iniciales | `units` → `products` + `warehouses` → `inventory` | Crea el saldo y los movimientos de inventario; las demás áreas consultan ese mismo saldo. |
| Compras contabilizadas | `suppliers` + `products` + `warehouses` → `purchases` | Crea recepción, documento, obligación de pago y asiento usando la configuración vigente de impuestos/costos. |
| Ventas contabilizadas | `customers` + `products` + `warehouses` + stock existente o `inventory` → `sales` | Crea salida, documento, cuenta por cobrar y asiento. Requiere existencias suficientes. |
| HCM | `employees` → `absences` / `attendance` | Relaciona ausencias y jornadas con el empleado; conserva la activación/aprobación posterior. |
| PM | `equipment` → `maintenance_notifications` / `maintenance_orders` | Relaciona avisos y órdenes con el equipo técnico. |
| TM | `vehicles` + `freight_orders` → `trips` | Relaciona vehículo y orden de transporte; el despacho mantiene sus controles. |
| PP | `products` → `bills_of_material` → `production_orders` | Relaciona producto terminado, componentes y orden de producción. |
| QM | `products` → `inspections` → `nonconformities` | Relaciona inspecciones y hallazgos. |
| PS | `projects` → `project_tasks` | Relaciona tareas, fechas y avance con el proyecto. |
| CO | `cost_centers` → `budgets` | Relaciona presupuesto con un centro de costo común. |
| MM, solicitudes y ofertas | `products` → `requisitions`; `suppliers` + solicitud → `supplier_quotes` | La selección de la oferta mantiene la aprobación de la solicitud. |
| SD, contratos | `customers` → `sales_contracts` | Vincula el contrato al cliente existente. |
| WM, tareas | `products` + `warehouses` → `warehouse_tasks` | Crea la tarea operativa; no duplica la existencia inicial. |
| CS | `customers` → `service_tickets` | Vincula incidencias de servicio con el cliente. |
| AA, CRM, EHS | `fixed_assets`, `opportunities`, `safety_incidents` | Crea los respectivos documentos iniciales. |

Si el maestro ya existe, **no vuelvas a cargarlo**: deja ese objeto sin archivo y referencia su código. La simulación busca primero en los datos de la empresa y en los maestros creados temporalmente dentro del mismo grupo. Las unidades y centros de costo idénticos pueden reutilizarse; los demás códigos existentes generan conflicto, sin sobrescritura.

Una plantilla no crea órdenes o personas en otros módulos por el solo hecho de cargar materiales. Evita duplicar el maestro; prepara las plantillas de los documentos que realmente existan. Los objetos que no necesitan datos en esa carga se dejan sin archivo.

## Campos, formato y efectos

- Conserva códigos, DNI/RUC y códigos de barras como **texto**, incluidos los ceros iniciales. Usa fechas `AAAA-MM-DD`; también se aceptan fechas reales de Excel. Los CSV usan UTF-8.
- Unidades y centros: `code`, `name`. Clientes/proveedores/almacenes: `code`, `name`; para compras/ventas añade la identidad fiscal única requerida por el documento.
- Materiales: `sku`, `name`; especifica una unidad activa. Si omites la unidad se aplica la convención heredada `NIU`, que debe existir. Precios/costos pueden ser cero. La categoría se crea si falta. Los otros campos industriales son opcionales o tienen reglas condicionales indicadas en el diccionario.
- Inventario: `number`, `sku`, `warehouse_code`, `quantity` entera distinta de cero, `reason`; `reference` puede usar `number` como respaldo. Las referencias ya registradas no se vuelven a cargar. Los materiales con lotes o series usan sus operaciones específicas de inventario.
- Ventas/compras: mismo `number` para varias líneas; contraparte, almacén y vencimiento consistentes entre ellas. No repitas SKU dentro del documento. La compra es una recepción contabilizada y la venta una salida contabilizada, no órdenes pendientes ni comprobantes enviados a SUNAT.
- Documentos empresariales: `number`, `title` y los campos obligatorios de su diccionario. Las referencias usan `_code` o `_sku`. No se importan estados aprobados.
- Lista de materiales: una fila por componente; repite `number`, `title`, `product_sku` y `base_quantity`; cambia `component_sku` y `component_quantity`.
- Límites: 5 MB por XLSX/CSV, 10 MB por ZIP, 2000 filas por grupo. Para más datos, divide por lotes y referencia los maestros ya migrados. Se rechazan fórmulas, encabezados desconocidos, paquetes con rutas y referencias ajenas a la empresa.
- No sumes una compra histórica al stock inicial si esa compra ya está incluida en ese saldo. Define una fecha de corte y carga solo las operaciones posteriores al saldo.
- HCM no calcula nómina; AA no contabiliza depreciación; PP no ejecuta consumos/producción automáticos; WM no mueve stock al cambiar únicamente el estado de una tarea. Cada módulo muestra su alcance y mantiene los comandos operativos especializados.

## Excel de prueba relacionados

Dentro del grupo: **Acciones del grupo → Descargar Excel de prueba (ZIP)** genera únicamente sus objetos y les asigna un prefijo común derivado del grupo. Sube ese mismo ZIP mediante **Cargar paquete ZIP**. No cambies códigos en un solo archivo si otros lo referencian.

También se entrega `ejemplos-migracion-DUNSDEMO.zip` en `examples/migrations/`: contiene 33 Excel con **35 filas ficticias**, dos materiales, un almacén, dos saldos iniciales, una compra, una venta y los 24 documentos empresariales. Crea un grupo con todos los grupos de módulos disponibles para usar el paquete completo. Con menos módulos, descarga el paquete generado por tu grupo.

Con los 33 objetos y una empresa de evaluación configurada, se esperan 35 altas. Si confirmas el paquete: el terminado pasa de 20 iniciales a 23 unidades (compra de 5, venta de 2); el componente queda en 100. Compras/ventas generan obligaciones y asientos. Los 24 documentos empresariales permanecen en su estado inicial; actívalos/libéralos desde cada módulo según su rol y flujo. El archivo no emite CPE.

Para repetir una prueba ya confirmada, crea un grupo nuevo y descarga su propio paquete con códigos nuevos. Simular repetidamente antes de confirmar no duplica datos.

## Ventanas de transacción

En los selectores compartidos de maestros y en las referencias de los módulos empresariales, pulsa el icono de **Entradas posibles** o **F4**. Busca código/descripción, marca la fila y pulsa **Seleccionar**. **Cancelar** conserva el valor anterior. La ventana se abre encima de la transacción sin cerrar el documento que estabas preparando.

## SQL y comprobación técnica

En esta instalación ya se ejecutaron `0065_enterprise_modules.sql` y `0066_migration_projects.sql`; los materiales ampliados usan `0064_material_master_profiles.sql`. No hace falta repetirlas.

Carpeta exacta: `D:\ERP_ZAMBRANO\ERP-enterprise-cloud\alembic\versions\`.

La migración 0066 añade dos tablas de preparación con RLS y permisos de API. No cambia tablas empresariales existentes. En instalaciones nuevas aplica las migraciones en orden; las credenciales administrativas solo se necesitan para DDL, nunca se envían al navegador.

Comprobación opcional del operador, usando el `.env` privado del backend:

```powershell
.venv\Scripts\python.exe -m scripts.check_migration_simulation --tenant-name "DUNS - Evaluación ERP"
```

Este comando prepara y simula ejemplos dentro de una transacción y ejecuta siempre rollback, incluidos el grupo temporal, sus archivos y auditorías. Las pruebas automatizadas usan una base aislada y verifican rollback, confirmación, idempotencia, roles, módulos, versiones y aislamiento de empresa.
