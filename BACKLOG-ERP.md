# Ruta pendiente — EnterpriseCloud ERP

> Actualizado: 25 de octubre de 2026. Estado: piloto funcional; este documento contiene solo trabajo pendiente y el orden recomendado.

## Criterios no negociables

- Toda consulta, comando, documento, exportación y búsqueda debe aislarse por `tenant_id`.
- Una tarea se cierra solo con pruebas unitarias, de integración, autorización y regresión visual cuando aplique.
- Las operaciones fiscales, contables, financieras e inventariables deben ser idempotentes, auditables y trazables hasta el documento origen.
- La firma actual es interna; no se presentará como firma cualificada o legal hasta integrar un proveedor de certificados.
- APISUNAT continuará como proveedor de facturación electrónica; no se implementará emisión SUNAT nativa.

## Prioridad 0 — Seguridad y operación

- [ ] Rotar credenciales usadas durante desarrollo (Supabase, Brevo, DeColecta, Gemini, APISUNAT y webhooks), mantenerlas solo como secretos de servidor y comprobar cada integración.
- [x] MFA TOTP desde Mi perfil y exigencia de sesión `aal2` para cambios de acceso, importaciones definitivas, aprobaciones firmadas, pagos ejecutados, ajustes ejecutados y anulaciones CPE. Ampliar la cobertura al crear comandos críticos nuevos.
- [ ] Separar el consumidor outbox en workers/colas para CPE, correo, importaciones, reportes, conciliaciones e integraciones externas; el worker embebido es transitorio.
- [ ] Definir backups automáticos, restauración verificada, retención, responsables, RPO/RTO y runbooks de incidentes.
- [ ] Añadir monitoreo y alertas para disponibilidad, 5xx, colas detenidas, CPE inciertos, correo, capacidad de base de datos y auditoría anómala.
- [ ] Evaluar enumeración/revocación por dispositivo con las capacidades del proveedor de identidad. La revocación global ya existe.
- [ ] Añadir a CI/CD escaneo de dependencias, pruebas de migración, smoke tests y despliegues graduales.

## Prioridad 1 — Flujo comercial e inventario

### Ventas

- [ ] Integrar nota de crédito CPE vinculada a un comprobante aceptado por APISUNAT. Mantener el bloqueo actual de devoluciones con CPE activo hasta completarlo.
- [ ] Formalizar máquina de estados y transiciones autorizadas para cotización, pedido, entrega, comprobante, cobro y devolución.
- [ ] Recuperar borradores de formularios ante recarga, navegación o fallo de red.
- [ ] Consolidar precios por cliente, moneda, lista de precios, descuento y aprobación excepcional.

### Compras

- [ ] Añadir solicitud de compra, cuadro comparativo y aprobación previa a la orden.
- [ ] Incorporar devoluciones parciales y tolerancias configurables de cantidad/precio con autorización.

### Inventario

- [ ] Reconstruir y conciliar existencias desde kardex, con reporte de diferencias y corrección controlada.
- [ ] Elegir, documentar y probar costeo promedio ponderado o FIFO.
- [ ] Reforzar lotes, series, vencimientos y alertas por sector del tenant.
- [ ] Ejecutar pruebas de concurrencia para reservas, recepciones, transferencias, ajustes y devoluciones.

## Prioridad 2 — Facturación, tesorería y contabilidad

### Facturación electrónica

- [ ] Modelar estados CPE: borrador, validado, reservado, enviado, aceptado, observado, rechazado e incierto.
- [ ] Persistir XML, CDR, PDF, hash, respuesta del proveedor y trazabilidad por comprobante.
- [ ] Reconsultar automáticamente comprobantes con timeout; nunca reemitir a ciegas.
- [ ] Implementar notas de crédito y débito con vínculo y validación del comprobante original.
- [ ] Terminar configuración fiscal por tenant: RUC, sucursal, series, numeración y credenciales APISUNAT cifradas.

### Tesorería

- [ ] Importar extractos bancarios y conciliar mediante reglas de matching.
- [ ] Crear cola de excepciones para pagos inciertos, duplicados, sin referencia o con diferencias.
- [ ] Incorporar flujo de caja proyectado, cobranza y alertas de mora.
- [ ] Aplicar doble control y segregación para pagos, beneficiarios nuevos y operaciones de riesgo.

### Contabilidad

- [ ] Garantizar en base de datos que cada asiento esté balanceado y ligado a su documento origen.
- [ ] Hacer inmutables los asientos publicados; corregir únicamente con reversos trazables.
- [ ] Cerrar periodos, permitir reapertura autorizada y registrar evidencia de auditoría.
- [ ] Incorporar PCGE, centros de costo, dimensiones y diferencias de cambio cuando corresponda.
- [ ] Generar conciliación diaria subledger–libro mayor y reportes reproducibles de diario, mayor, balance y cierre.

## Prioridad 3 — Aprobaciones, documentos y firma

- [ ] Configurar matrices de aprobación por módulo, importe, almacén, centro de costo, área y riesgo.
- [ ] Agregar delegación temporal, SLA, escalamiento, recordatorios y suplencias auditables.
- [ ] Extender segregación de funciones para pagos, descuentos, ajustes de stock y órdenes de compra.
- [ ] Versionar documentos, adjuntos, hashes, estados y evidencia de firma de modo inmutable.
- [ ] Crear pantalla de verificación de documentos firmados internamente.
- [ ] Integrar certificado y firma digital cualificada cuando se seleccione proveedor legal.

## Prioridad 4 — Datos maestros, importación y experiencia

- [ ] Versionar impuestos, monedas, tipos de cambio, unidades SUNAT, listas de precios y condiciones de pago.
- [ ] Detectar, fusionar y auditar duplicados de clientes, proveedores y productos.
- [ ] Completar catálogos peruanos: documentos, ubigeo, unidades, afectación IGV y motivos de notas/traslados.
- [ ] Mejorar Excel: simulación, prevalidación, mapeos reutilizables, errores por fila, idempotencia y reversión.
- [ ] Sustituir selects nativos restantes, especialmente en Compras, por buscadores con modal y tabla paginada.
- [ ] Estandarizar tablas, paginación, filtros, estados vacíos, modo oscuro y experiencia móvil.
- [ ] Añadir pruebas E2E visuales antes de cada despliegue.

## Prioridad 5 — Reportes, IA y plataforma SaaS

- [ ] Formalizar diccionario de KPIs y filtros obligatorios por tenant, periodo, moneda y estado.
- [ ] Ejecutar reportes y exportaciones pesadas en segundo plano, con enlaces temporales y auditoría.
- [ ] Completar paneles por rol y reportes programados.
- [ ] Robustecer RAG: sincronización durable, filtros por tenant/rol/módulo, frescura de información y métricas de calidad.
- [ ] Mantener IA como asistencia: nunca debe ejecutar sola acciones financieras, contables o fiscales.
- [ ] Implementar cuotas, planes, onboarding/offboarding, exportación y retención por tenant.

## Orden óptimo

1. Rotación de secretos, MFA/reauth, respaldo y monitoreo.
2. Estados CPE y resiliencia APISUNAT; nota de crédito CPE.
3. Concurrencia y costeo de inventario; estados comerciales y borradores.
4. Tesorería, conciliación y cierres contables.
5. Aprobaciones documentales, firma verificable y segregación ampliada.
6. Datos maestros, importación y experiencia.
7. Workers dedicados, reportes asíncronos, IA y capacidades SaaS.

## Dependencias externas y fuera de alcance actual

- La rotación real de secretos requiere cambiarlos en cada proveedor antes de actualizar Render/Vercel.
- Facturación SUNAT nativa: fuera de alcance; se usa APISUNAT.
- Firma con certificado y validez legal: pendiente de proveedor seleccionado.
- Protección de contraseñas filtradas de Supabase: excluida por decisión de costos.
- Nómina/RR. HH., manufactura/MRP, consolidación empresarial y microservicios: fases futuras.
