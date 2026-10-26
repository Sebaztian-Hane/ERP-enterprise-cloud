# Pendientes actualizados — EnterpriseCloud ERP

> Actualizado: 25 de octubre de 2026.
>
> Estado: piloto funcional multi-tenant. Este archivo contiene trabajo pendiente,
> riesgos abiertos y el orden recomendado; no reabre entregables ya verificados.

## Criterios no negociables

- Todo dato, archivo, búsqueda, exportación y evento se aísla por `tenant_id`.
  Entidad legal, sucursal y almacén complementan esa frontera; no la reemplazan.
- Dinero, stock, CPE y contabilidad deben ser idempotentes, auditables y
  recuperables luego de un error o reinicio.
- Una tarea requiere pruebas de dominio, permisos/BOLA, regresión visual cuando
  aplique y evidencia de despliegue; compilar no es suficiente.
- APISUNAT sigue siendo el adaptador fiscal; no se implementará SUNAT nativo
  sin una decisión explícita de arquitectura.
- La firma actual es interna y no se presentará como cualificada/legal hasta
  integrar un proveedor de certificado.

## Entregado recientemente

- [x] Expediente CPE privado: respuesta del proveedor, XML, CDR y PDF, hash
  SHA-256, tamaño, contenido y fecha de captura.
- [x] Trazabilidad append-only de reserva, envío, consulta, anulación e
  incidentes de entrega, con RLS y descargas temporales autorizadas.
- [x] Estados CPE `reserved`, `submitted`, `observed` y `unknown` corregidos a
  nivel de base de datos.
- [x] Interfaz de Ventas para revisar y descargar evidencia privada, sin
  exponer enlaces directos del proveedor.
- [x] `cdn.apisunat.com` verificado como host CPE permitido; otros hosts se
  bloquean para prevenir SSRF.

---

## P0 — Seguridad, continuidad y operación

- [ ] Rotar secretos usados durante desarrollo: Supabase, Brevo, DeColecta,
  Gemini, APISUNAT, n8n/HMAC y claves de cifrado. Mantenerlos solo en secretos
  de servidor y registrar la rotación sin guardar sus valores.
- [ ] Ejecutar pruebas BOLA/IDOR y RLS para rutas, descargas, exportaciones,
  adjuntos, CPE y módulos nuevos usando al menos dos tenants.
- [ ] Separar el consumidor outbox del proceso API: workers/colas dedicados
  para CPE, correo, importaciones, reportes, conciliaciones e integraciones.
- [ ] Definir backup automático, restauración comprobada, retención, RPO/RTO y
  runbooks de incidentes.
- [ ] Implementar logs estructurados, correlation IDs, métricas y alertas de
  5xx, colas detenidas, CPE inciertos, fallos de correo y auditoría anómala.
- [ ] Completar CI/CD con lint, tipos, migraciones, SAST/SCA, detección de
  secretos, SBOM, smoke tests y despliegue gradual.
- [ ] Evaluar control de sesiones/dispositivos y revocación granular. La
  revocación global ya existe.

> Excluido por costo: protección de Supabase contra contraseñas filtradas. No
> es bloqueo para las demás tareas de seguridad.

## P1 — Flujo comercial e inventario

### Ventas y compras

- [ ] Completar: cotización → pedido → reserva → despacho/entrega → comprobante
  → cobro → devolución/nota.
- [ ] Implementar nota de crédito y débito CPE vinculada al comprobante
  aceptado. Requiere la plantilla/contrato APISUNAT para referencia al CPE
  origen y discrepancia; no inferir el JSON.
- [ ] Añadir solicitud de compra, cuadro comparativo y aprobación previa a la
  orden de compra.
- [ ] Implementar validación de tres vías: orden ↔ recepción ↔ factura del
  proveedor.
- [ ] Incorporar devoluciones parciales y tolerancias configurables de
  cantidad/precio con autorización.
- [ ] Consolidar precios por cliente, moneda, vigencia, lista, descuento y
  aprobación excepcional.

### Inventario

- [ ] Ejecutar pruebas de concurrencia de reservas, recepciones, despachos,
  transferencias, conteos, ajustes y devoluciones.
- [ ] Completar stock en tránsito, merma, cuarentena, devoluciones y motivos
  obligatorios con evidencia para cada ajuste.
- [ ] Crear alertas de caducidad, lote, serie y stock mínimo por sector/tenant.
- [ ] Validar costeo promedio/FIFO por almacén y período antes de usar costos
  en reportes financieros.

## P2 — Facturación, tesorería y contabilidad

### Facturación electrónica

- [ ] Definir política de retención/ciclo de vida para XML, CDR, PDF, hashes y
  respuestas: exportación controlada, recuperación de huérfanos y borrado legal.
- [ ] Reconsultar CPE `submitted` o `unknown` de forma automática, acotada e
  idempotente; nunca reemitir a ciegas tras timeout.
- [ ] Agregar fixtures/contratos APISUNAT para aceptación, observación,
  rechazo, timeout, duplicado, PDF/XML/CDR no disponible y error de descarga.
- [ ] Finalizar configuración fiscal por entidad legal/tenant: RUC, sucursal,
  series, numeración y credenciales APISUNAT cifradas.
- [ ] Implementar notas de crédito/débito y conciliación fiscal posterior a
  anulaciones o devoluciones.

### Tesorería

- [ ] Importar extractos bancarios y conciliar con reglas de referencia,
  importe, fecha y tolerancia.
- [ ] Crear cola de excepciones para pagos inciertos, duplicados, sin referencia
  o con diferencias; no marcarlos como pagados automáticamente.
- [ ] Incorporar flujo de caja proyectado, cobranza, vencimientos y alertas de
  mora.
- [ ] Aplicar doble control y segregación para pagos, beneficiarios nuevos y
  operaciones de riesgo.

### Contabilidad

- [ ] Garantizar en base que un asiento publicado balancee `debe = haber` y
  esté ligado a un documento origen.
- [ ] Hacer inmutables los asientos publicados; corregir solo con reversos
  trazables.
- [ ] Cerrar períodos, permitir reapertura autorizada y conservar auditoría.
- [ ] Completar PCGE, dimensiones, moneda/tipo de cambio y reglas contables por
  evento de negocio.
- [ ] Conciliar subledger–libro mayor y generar diario, mayor, balance, cierre
  y reportes reproducibles.

## P3 — Aprobaciones, documentos y firma

- [ ] Configurar matrices por módulo, importe, entidad legal, almacén, centro
  de costo, área y riesgo.
- [ ] Completar delegación temporal, SLA, escalamiento, recordatorios y
  suplencias auditables.
- [ ] Extender segregación de funciones a pagos, descuentos, ajustes de stock
  y órdenes de compra de alto riesgo.
- [ ] Versionar documentos/adjuntos operativos con contenido/hash inmutable,
  estados y vínculo de aprobación.
- [ ] Consolidar la pantalla de verificación de documentos y firmas internas.
- [ ] Integrar certificado/firma cualificada al seleccionar proveedor; conservar
  certificado, cadena, sello de tiempo y evidencia verificable.

## P4 — Maestros, importación y experiencia

- [ ] Versionar impuestos, monedas, tipos de cambio, unidades SUNAT, listas de
  precios, condiciones de pago y políticas de descuento.
- [ ] Detectar, fusionar y auditar duplicados de clientes, proveedores y
  productos.
- [ ] Completar catálogos peruanos: documentos, ubigeo, unidades, afectación
  IGV y motivos de notas/traslados.
- [ ] Mejorar Excel: simulación, prevalidación, mapeos reutilizables, errores
  por fila, idempotencia y reversión.
- [ ] Sustituir selects nativos pendientes por buscadores con modal, tabla
  paginada y filtros del servidor.
- [ ] Estandarizar tablas, paginación, estados vacíos, modo oscuro y móvil;
  añadir regresión visual E2E antes de desplegar.

## P5 — Reportes, IA y SaaS

- [ ] Formalizar diccionario de KPIs y filtros por tenant, entidad legal,
  período, moneda y estado.
- [ ] Ejecutar reportes/exportaciones pesadas en segundo plano con descarga
  temporal y auditoría.
- [ ] Completar paneles por rol y reportes programados.
- [ ] Robustecer RAG: sincronización durable, filtros por tenant/rol/módulo,
  frescura de datos y métricas de calidad.
- [ ] Mantener IA en modo asistido: puede recomendar, nunca ejecutar por sí
  sola acciones financieras, contables, fiscales o de inventario.
- [ ] Completar cuotas, planes, onboarding/offboarding, exportación, retención
  y borrado por tenant.

## Orden óptimo

1. Seguridad y operación: rotación, respaldo/restauración, monitoreo, BOLA/RLS
   y worker separado.
2. Cierre fiscal seguro: reconsulta CPE, retención, contratos APISUNAT y notas.
3. Flujo comercial/inventario: tres vías, despacho, devoluciones, concurrencia
   y costeo validado.
4. Tesorería/contabilidad: bancos, excepciones, cierres y conciliación GL.
5. Aprobaciones formales: matrices, SoD, documentos/versionado y firma.
6. Datos, UX y escala: maestros, importaciones, reportes, IA y SaaS.

## Dependencias externas y fuera de alcance actual

- La rotación real de secretos requiere emitir/revocar claves en cada proveedor
  y después actualizar Render/Vercel.
- APISUNAT continuará manejando emisión ante SUNAT; no se desarrollará
  integración SUNAT nativa.
- La firma con certificado y validez legal depende del proveedor aún no elegido.
- Nómina/RR.HH., manufactura/MRP, consolidación y microservicios son fases
  futuras, no requisitos del piloto actual.
