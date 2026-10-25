# Ruta pendiente — EnterpriseCloud ERP

> Actualizado: 25 de octubre de 2026. Estado: piloto funcional con flujo comercial e inventario base operativos. Este documento contiene **solo trabajo pendiente** y su secuencia recomendada.

## Criterios no negociables

- Cada consulta, comando, archivo, exportación y búsqueda debe aislarse por `tenant_id`.
- Una tarea se cierra únicamente con pruebas unitarias, de integración, autorización y regresión visual cuando aplique.
- Las operaciones de inventario, tesorería, contabilidad y fiscalidad deben ser idempotentes, auditables y trazables hasta su documento de origen.
- La firma actual es interna y no se presentará como firma digital cualificada o legal hasta integrar un proveedor de certificados.
- APISUNAT seguirá siendo el proveedor de facturación electrónica; no se implementará emisión SUNAT nativa.

## Prioridad 0 — Seguridad y operación

### Pendiente inmediato

- [ ] Rotar las credenciales que se usaron durante desarrollo (Supabase, Brevo, DeColecta, Gemini, APISUNAT y webhooks). Guardarlas únicamente como secretos de servidor y verificar cada integración después de rotarla.
- [ ] Añadir MFA o reautenticación reciente para propietario, administrador, tesorería y contabilidad; exigirla antes de pagos, cambios fiscales, cambios de acceso y acciones irreversibles.
- [ ] Separar el consumidor de outbox en workers/colas para CPE, correos, importaciones, reportes, conciliaciones e integraciones externas. El worker embebido actual es una solución transitoria.
- [ ] Definir backups automáticos, una restauración de prueba periódica, retención, responsables, RPO/RTO y runbooks de incidente.
- [ ] Incorporar monitoreo y alertas: disponibilidad, errores 5xx, colas detenidas, CPE inciertos, fallas de correo, consumo de base de datos y auditoría anómala.

### Pendiente posterior

- [ ] Evaluar enumeración/revocación por dispositivo según las capacidades del proveedor de identidad. La revocación global ya existe, pero no existe un listado fiable de sesiones remotas.
- [ ] Añadir escaneo de dependencias, prueba de migraciones, smoke tests y despliegues graduales en CI/CD.

## Prioridad 1 — Cierre del flujo comercial e inventario

### Ventas

- [ ] Integrar nota de crédito CPE vinculada a un comprobante aceptado por APISUNAT. Hasta entonces debe mantenerse el bloqueo de devolución operativa para ventas con CPE activo.
- [ ] Formalizar máquina de estados y transiciones autorizadas para cotización, pedido, entrega, comprobante, cobro y devolución.
- [ ] Recuperar borradores de formularios ante recarga, navegación o error de red.
- [ ] Consolidar precios por cliente, moneda, lista de precios, descuento y aprobación excepcional.

### Compras

- [ ] Añadir solicitud de compra, cuadro comparativo de proveedores y aprobación previa a la orden.
- [ ] Incorporar devoluciones parciales y tolerancias configurables de cantidad/precio con autorización.

### Inventario

- [ ] Reconstruir y conciliar existencias desde kardex, con reporte de diferencias y corrección controlada.
- [ ] Elegir, documentar y probar la política de costeo aplicable: promedio ponderado o FIFO.
- [ ] Reforzar lotes, series, vencimientos y alertas de acuerdo con el sector de cada tenant.
- [ ] Ejecutar pruebas de concurrencia para reservas, recepciones, transferencias, ajustes y devoluciones.

## Prioridad 2 — Facturación, tesorería y contabilidad

### Facturación electrónica

- [ ] Modelar estados CPE: borrador, validado, reservado, enviado, aceptado, observado, rechazado e incierto.
- [ ] Persistir XML, CDR, PDF, hash, respuesta del proveedor y trazabilidad por cada comprobante.
- [ ] Reconsultar automáticamente comprobantes con timeout; nunca reemitir ciegamente.
- [ ] Implementar notas de crédito y débito con vínculo y validaciones contra el comprobante original.
- [ ] Terminar configuración fiscal por tenant: RUC, sucursal, series, numeración y credenciales APISUNAT cifradas.

### Tesorería

- [ ] Importar extractos bancarios y conciliar mediante reglas de matching.
- [ ] Crear una cola de excepciones para pagos inciertos, duplicados, sin referencia o con diferencia.
- [ ] Incorporar flujo de caja proyectado, cobranza y alertas de mora.
- [ ] Aplicar doble control y segregación de funciones para pagos, beneficiarios nuevos y operaciones de riesgo.

### Contabilidad

- [ ] Garantizar en base de datos que cada asiento esté balanceado y vinculado a su documento origen.
- [ ] Hacer inmutables los asientos publicados; las correcciones deben generar reversos trazables.
- [ ] Cerrar periodos y permitir reapertura solo con autorización y auditoría.
- [ ] Incorporar PCGE, centros de costo, dimensiones y diferencias de cambio cuando corresponda.
- [ ] Generar conciliación diaria subledger–libro mayor y reportes reproducibles de diario, mayor, balance y cierre.

## Prioridad 3 — Aprobaciones, documentos y firma

- [ ] Configurar matrices de aprobación por módulo, importe, almacén, centro de costo, área y riesgo.
- [ ] Agregar delegación temporal, SLA, escalamiento, recordatorios y suplencias auditables.
- [ ] Extender segregación de funciones para pagos, descuentos, ajustes de stock y órdenes de compra.
- [ ] Versionar documentos, adjuntos, hashes, estados y evidencia de firma de forma inmutable.
- [ ] Crear la pantalla de verificación de documentos firmados internamente.
- [ ] Integrar certificado y firma digital cualificada cuando se seleccione el proveedor legal.

## Prioridad 4 — Datos maestros, importación y experiencia

- [ ] Versionar impuestos, monedas, tipos de cambio, unidades SUNAT, listas de precios y condiciones de pago.
- [ ] Detectar, fusionar y auditar duplicados de clientes, proveedores y productos.
- [ ] Completar catálogos peruanos: documentos, ubigeo, unidades, afectación IGV y motivos de notas/traslados.
- [ ] Mejorar importación Excel: simulación, prevalidación, mapeos reutilizables, errores por fila, idempotencia y reversión.
- [ ] Sustituir selects nativos restantes, especialmente en Compras, por buscadores con modal y tabla paginada.
- [ ] Estandarizar tablas, paginación, filtros, estados vacíos, modo oscuro y experiencia móvil.
- [ ] Añadir pruebas E2E visuales previas a cada despliegue.

## Prioridad 5 — Reportes, IA y plataforma SaaS

- [ ] Formalizar diccionario de KPIs y filtros obligatorios por tenant, periodo, moneda y estado.
- [ ] Ejecutar reportes y exportaciones pesadas en segundo plano, con enlaces temporales y auditoría.
- [ ] Completar paneles por rol y reportes programados.
- [ ] Robustecer RAG: sincronización durable, filtros por tenant/rol/módulo, información desactualizada y métricas de calidad.
- [ ] Mantener la IA solo como asistente: nunca debe ejecutar por sí misma acciones financieras, contables o fiscales.
- [ ] Implementar cuotas, planes, onboarding/offboarding, exportación y retención de datos por tenant.

## Orden óptimo de ejecución

1. Rotación de secretos, MFA/reauth, respaldo y monitoreo.
2. Estados CPE y resiliencia APISUNAT; nota de crédito CPE.
3. Concurrencia y costeo de inventario; estados comerciales y recuperación de borradores.
4. Tesorería, conciliación y cierres contables.
5. Aprobaciones documentales, firma interna verificable y segregación ampliada.
6. Datos maestros, importación y experiencia de usuario.
7. Workers dedicados, reportes asíncronos, IA y capacidades SaaS.

## Dependencias externas o fuera de alcance actual

- Rotación real de secretos: necesita ejecutar el cambio en cada proveedor antes de actualizar Render/Vercel.
- Facturación SUNAT nativa: fuera de alcance; se mantiene APISUNAT.
- Firma con certificado y validez legal: pendiente de proveedor seleccionado.
- Protección de contraseñas filtradas de Supabase: excluida por decisión de costos.
- Nómina/RR. HH., manufactura/MRP, consolidación empresarial y microservicios: fases futuras.
