# Backlog de implementación — EnterpriseCloud ERP

> Estado: piloto funcional. Este documento reúne el trabajo restante para llevar el ERP a una operación empresarial segura, trazable y escalable.

## Criterios permanentes

- Todo dato y toda operación deben respetar el aislamiento por `tenant_id`.
- Una solución se considera terminada solo con pruebas funcionales, de seguridad y de regresión visual cuando aplique.
- Las acciones financieras, contables, fiscales y de inventario deben ser trazables, autorizables e idempotentes.
- La firma actual es interna; no debe presentarse como firma digital cualificada o con validez legal hasta integrar un proveedor de certificados.
- La facturación electrónica se mantiene mediante APISUNAT; no se desarrollará facturación SUNAT nativa.

## Prioridad 0 — Seguridad y operación

- [ ] Auditar aislamiento por `tenant_id` en APIs, RLS, documentos, exportaciones y búsquedas.
- [ ] Ejecutar pruebas BOLA/IDOR para impedir accesos cruzados entre tenants.
- [ ] Rotar credenciales usadas durante desarrollo y conservarlas únicamente como secretos del servidor.
- [ ] Incorporar MFA o reautenticación para dueños, administradores, tesorería, contabilidad y acciones irreversibles.
- [ ] Añadir gestión de sesiones: dispositivos activos, revocación de sesiones y rotación de llaves de firma interna.
- [ ] Separar workers/colas para CPE, correos, importaciones, reportes, conciliaciones e integraciones externas.
- [ ] Implementar backups, restauraciones verificadas, monitoreo, alertas y runbooks de incidentes.

## Prioridad 1 — Flujo comercial e inventario

### Ventas

- [ ] Completar el flujo: cotización → pedido → entrega → comprobante → cobro → devolución/nota de crédito.
- [ ] Definir estados y transiciones autorizadas para cada documento comercial.
- [ ] Recuperar borradores de formularios ante navegación, recarga o fallos de red.
- [ ] Consolidar precios por cliente, moneda, lista de precios, descuentos y aprobaciones excepcionales.

### Compras

- [ ] Completar el flujo: solicitud → comparación → orden de compra → recepción → factura de proveedor → pago.
- [ ] Implementar matching de tres vías entre orden de compra, recepción y factura del proveedor.
- [ ] Gestionar devoluciones, diferencias de cantidad/precio y tolerancias con aprobación.

### Inventario

- [ ] Implementar y probar reservas, stock en tránsito, cuarentena, mermas, devoluciones y ajustes con motivo obligatorio.
- [ ] Añadir reconstrucción y conciliación de stock a partir del kardex.
- [ ] Definir y probar política de costeo promedio/FIFO.
- [ ] Robustecer lotes, series, vencimientos y alertas según el sector del tenant.
- [ ] Ejecutar pruebas de concurrencia sobre movimientos, reservas y ajustes de stock.

## Prioridad 2 — Facturación, tesorería y contabilidad

### Facturación electrónica

- [ ] Formalizar estados CPE: borrador, validado, reservado, enviado, aceptado, observado, rechazado y estado incierto.
- [ ] Guardar XML, CDR, PDF, hash, respuesta del proveedor y trazabilidad completa por comprobante.
- [ ] Reconsultar automáticamente comprobantes con timeout; nunca reemitir de forma ciega.
- [ ] Implementar notas de crédito/débito con vínculo y validaciones respecto al comprobante original.
- [ ] Completar configuración fiscal por tenant: RUC, sucursal, series, numeraciones, `personaId` y token APISUNAT cifrado.

### Tesorería

- [ ] Importar extractos bancarios y ejecutar conciliación mediante reglas de matching.
- [ ] Crear cola de excepciones: pagos inciertos, duplicados, sin referencia o con diferencias.
- [ ] Incorporar flujo de caja proyectado, cobranza y alertas de mora.
- [ ] Aplicar aprobación y segregación de funciones para pagos, nuevos beneficiarios y pagos por importe/riesgo.

### Contabilidad

- [ ] Garantizar en base de datos que todo asiento cumpla debe = haber y tenga documento de origen.
- [ ] Hacer inmutables los asientos publicados; las correcciones deben ser reversos trazables.
- [ ] Cerrar periodos, con reapertura autorizada y evidencia de auditoría.
- [ ] Incorporar PCGE, dimensiones/centros de costo y diferencias de cambio cuando corresponda.
- [ ] Generar conciliación diaria subledger–libro mayor y reportes de libro diario, mayor, balance y cierre reproducible.

## Prioridad 3 — Aprobaciones, documentos y firma

- [ ] Configurar matrices de aprobación por módulo, importe, almacén, centro de costo, área y nivel de riesgo.
- [ ] Implementar delegación temporal, SLA, escalamiento, recordatorios y suplencias auditables.
- [ ] Extender segregación de funciones a pagos, ajustes de stock, descuentos y órdenes de compra.
- [ ] Versionar documentos, adjuntos, hashes, estados de aprobación y evidencia de firma de modo inmutable.
- [ ] Crear pantalla de verificación para documentos firmados internamente.
- [ ] Integrar certificado y firma digital cualificada cuando se seleccione el proveedor legal.

## Prioridad 4 — Datos maestros, importación y experiencia de usuario

- [ ] Versionar impuestos, monedas, tipos de cambio, unidades SUNAT, listas de precios y condiciones de pago.
- [ ] Detectar, fusionar y auditar duplicados de clientes, proveedores y productos.
- [ ] Completar catálogos peruanos: documentos, ubigeo, unidades, afectación IGV y motivos de notas/traslados.
- [ ] Mejorar importaciones Excel: prevalidación, simulación, mapeos reutilizables, errores por fila, idempotencia y reversión.
- [ ] Reemplazar selects nativos restantes —especialmente en Compras— por buscadores en modal con tabla paginada.
- [ ] Estandarizar tablas, paginación, filtros, estados vacíos, modo oscuro y experiencia móvil.
- [ ] Añadir pruebas E2E visuales para prevenir regresiones de frontend antes de cada despliegue.

## Prioridad 5 — Reportes, IA y plataforma SaaS

- [ ] Formalizar diccionario de KPIs y filtros obligatorios por empresa, periodo, moneda y estado.
- [ ] Ejecutar reportes y exportaciones pesadas de forma asíncrona, con enlaces temporales y auditoría.
- [ ] Completar paneles por rol y reportes programados.
- [ ] Robustecer RAG: sincronización durable, filtros por tenant/rol/módulo, detección de información desactualizada y métricas de calidad.
- [ ] Mantener la IA como asistencia: nunca debe ejecutar por sí sola acciones financieras, contables o fiscales.
- [ ] Implementar cuotas por tenant, planes, activación de módulos, onboarding/offboarding, exportación y retención de datos.
- [ ] Reforzar CI/CD: pruebas de migraciones, seguridad, dependencias, secretos, smoke tests y despliegues graduales.

## Orden recomendado de ejecución

1. Seguridad, RLS/BOLA/IDOR, rotación de secretos y recuperación operativa.
2. Cierre integral de ventas, compras e inventario con pruebas de concurrencia.
3. Estados CPE, resiliencia con APISUNAT y tesorería/conciliación.
4. Cierre contable, aprobaciones documentales y segregación de funciones.
5. Workers, backups, monitoreo y pruebas E2E para preparación productiva.

## Fuera de alcance actual

- Facturación SUNAT nativa: se mantiene APISUNAT como proveedor externo.
- Firma con certificado y validez legal: pendiente de elegir e integrar el proveedor.
- Protección de contraseñas filtradas de Supabase: excluida por decisión de costos.
- Nómina/RR. HH., manufactura/MRP, consolidación empresarial y microservicios: fases futuras.
