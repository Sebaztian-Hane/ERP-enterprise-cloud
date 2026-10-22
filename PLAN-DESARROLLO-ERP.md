# Plan de desarrollo — EnterpriseCloud ERP

> Estado: hoja de ruta para pasar del piloto actual a una operación empresarial
> verificable. Basada en `ERP-FUNDAMENTS-ARCHITECTURE` y en la auditoría del
> proyecto del 21 de octubre de 2026.

## Principios que no se negocian

- La corrección contable, fiscal, de stock y de aislamiento entre empresas va
  antes que una nueva pantalla o integración.
- Cada operación que genere dinero, stock, documento fiscal o asiento debe ser
  idempotente, auditable y recuperable tras un fallo.
- `tenant_id` continúa como frontera SaaS. No se reemplazará sin una migración
  formal; se ampliará con entidad legal/RUC, sucursal y almacén.
- n8n, RAG e IA son auxiliares: no confirman pagos, asientos, stock ni CPE.
- La integración externa de facturación electrónica se mantiene. No se
  construirá una integración SUNAT nativa salvo cambio explícito de estrategia.
- Una tarea no está terminada solo porque compila: requiere pruebas, permisos,
  telemetría, migración segura y evidencia funcional.

## Línea base actual

### Capacidades ya presentes

- Aislamiento inicial por `tenant_id`, membresías, permisos y políticas RLS.
- Roles, habilitación de módulos por empresa/usuario y consola de proveedor.
- Clientes, proveedores, productos, almacenes, ventas, compras, inventario,
  tesorería, cuentas por cobrar/pagar, documentos y reportes.
- Reservas, transferencias, conteo físico, lotes/series y trazabilidad de
  inventario en una primera versión.
- Aprobaciones y documentos inmutables con firma interna criptográfica.
- Pagos parciales, conciliación bancaria básica, plan de cuentas, diarios y
  períodos contables iniciales.
- Integraciones DeColecta, Brevo, Apisunat de desarrollo, Gemini y RAG/n8n.
- Paginación, importación de Excel y pruebas automatizadas iniciales.

### Límites actuales

- La firma interna **no** sustituye un certificado ni una firma digital con
  validez legal.
- La emisión electrónica aún es un flujo de desarrollo; no es una operación
  fiscal productiva completa.
- El despliegue gratuito usa un consumidor embebido con outbox persistente para
  RAG y CPE. El worker separado, la observabilidad y los SLO siguen pendientes.

---

## Ruta óptima de desarrollo

La ruta está ordenada por dependencias. No iniciar una fase posterior si su
gate no está aprobado.

```text
Fundación verificable
        ↓
Vertical comercial completo
        ↓
Fiscal y contabilidad conciliados
        ↓
Piloto operativo controlado
        ↓
Escala SaaS, automatización y analítica avanzada
```

## Fase 0 — Estabilización y gobierno técnico

**Objetivo:** crear una base que permita evolucionar sin perder datos ni
seguridad.

- [ ] Crear ADRs vivos para tenancy, stock, contabilidad, CPE, IA, firma e
      integración bancaria.
- [ ] Definir dueños funcionales y técnicos para ventas, compras, inventario,
      tesorería, contabilidad, fiscal y seguridad.
- [ ] Crear matriz `requisito → regla → API/SQL → prueba → evidencia`.
- [ ] Documentar BPMN o flujos equivalentes para venta, compra, recepción,
      ajuste de inventario, cobro, pago, CPE y cierre mensual.
- [ ] Inventariar datos personales, secretos, proveedores externos y reglas
      legales aplicables por versión/vigencia.
- [ ] Rotar todas las credenciales compartidas durante el desarrollo y revisar
      Git, Render, Vercel, n8n, logs y variables públicas.
- [ ] Establecer Definition of Ready / Done para cambios de alto riesgo.

**Gate F0:** responsables asignados, arquitectura documentada, secretos
rotados y ningún cambio financiero sin regla/proceso/prueba identificable.

## Fase 1 — Integridad transaccional y multi-tenancy

**Objetivo:** demostrar que las operaciones no se duplican, no cruzan empresas
y no quedan parcialmente aplicadas.

**Avance actual:** ya existe la infraestructura persistente de idempotencia y
está aplicada a ventas, recepción de compras, pagos y comandos de inventario
(reservas, conteos, ajustes y transferencias). Los eventos de RAG y la reserva
de CPE usan una outbox transaccional con recuperación de leases. Los commits de
importación también son idempotentes por hash del archivo. En Render Free el
consumidor se ejecuta dentro del API; un worker separado sigue pendiente.

- [x] Implementar almacenamiento transversal de `Idempotency-Key` para ventas,
      pagos, transferencias, recepciones, importaciones y CPE.
- [x] Rechazar una misma clave con payload distinto y devolver el resultado
      original en reintentos legítimos.
- [x] Reemplazar eventos en hilos efímeros por patrón **transactional outbox**.
- [x] Añadir consumidor/inbox idempotente, reintentos acotados, dead-letter
      queue y trazabilidad de eventos externos.
- [ ] Crear pruebas BOLA/IDOR y RLS para todas las rutas, tablas, documentos y
      exportaciones; incluir ataques con otro `tenant_id`.
- [ ] Validar segregación de funciones: creador, aprobador y ejecutor no deben
      coincidir cuando la política lo prohíba.
- [ ] Añadir MFA y reautenticación para dueño, administrador, tesorería,
      contabilidad, gestión de claves y acciones irreversibles.
- [ ] Añadir revocación de sesiones, gestión/rotación/revocación de llaves de
      firma interna y registro de dispositivos.

**Gate F1:** pruebas concurrentes y cross-tenant verdes; cero duplicados en
operaciones repetidas; eventos recuperables después de reiniciar un proceso.

## Fase 2 — Modelo empresarial y maestros

**Objetivo:** separar correctamente la plataforma SaaS de las entidades que
operan y tributan.

**Avance actual:** se incorporó el maestro de entidades legales (RUC, régimen,
dirección fiscal) y sucursales, asociado al `tenant_id`. Los almacenes ya
admiten una futura asociación a sucursal. Todavía falta hacer obligatoria esa
selección en documentos, secuencias y configuración fiscal.

- [ ] Modelar entidad legal, RUC, régimen, establecimiento/sucursal, dirección
      fiscal y almacén; relacionarlos con el tenant actual.
- [ ] Alcanzar numeración por entidad legal, tipo documental, serie y
      establecimiento, no solo por tenant.
- [ ] Versionar impuestos, monedas, tipos de cambio, unidades SUNAT, listas de
      precio, condiciones de pago y políticas de descuento.
- [ ] Definir centros de costo/dimensiones contables y su validación por rol.
- [ ] Añadir reglas de calidad, deduplicación, consolidación controlada y
      trazabilidad de cliente/proveedor/producto.
- [ ] Completar catálogos peruanos necesarios: tipo de documento, UBIGEO,
      unidades, afectación IGV y motivos de nota/traslado.
- [ ] Mejorar importaciones con prevalidación, modo simulación, mapeos
      reutilizables, reporte de errores por fila, idempotencia y reversión.

**Gate F2:** una empresa puede tener varias entidades legales/sucursales sin
mezclar datos, correlativos ni configuraciones tributarias.

## Fase 3 — Vertical comercial e inventario

**Objetivo:** cerrar de forma controlada el circuito pedido → stock → cobro.

### Ventas

- [ ] Completar cotización, pedido, despacho/entrega, factura/boleta, cobro,
      devolución y nota de crédito.
- [ ] Incorporar precios por lista, vigencia, cliente, moneda, descuentos y
      límites que requieran aprobación.
- [ ] Persistir borradores y recuperación ante red/navegación para formularios
      operativos.
- [ ] Mostrar estados inequívocos: borrador, confirmado, reservado, despachado,
      pendiente externo, aceptado, rechazado, anulado o revertido.

### Compras y proveedores

- [ ] Completar solicitud de compra, comparación/cotización, orden de compra,
      recepción, factura de proveedor, cuenta por pagar y pago.
- [ ] Implementar validación de tres vías: orden ↔ recepción ↔ factura.
- [ ] Gestionar devoluciones, diferencias de cantidad/precio y tolerancias
      aprobadas.

### Inventario

- [ ] Verificar invariantes de stock en concurrencia para recepción, reserva,
      despacho, transferencia, conteo y ajuste.
- [ ] Implementar stock en tránsito, devoluciones, merma, cuarentena y motivo
      obligatorio para ajustes.
- [ ] Completar operación de lotes, series, vencimientos y alertas de caducidad
      según el sector del cliente.
- [ ] Reconstruir y conciliar el stock desde el kardex/ledger contra snapshots.
- [ ] Definir y probar política de costeo: promedio, FIFO u otra, sin mezclar
      resultados entre almacenes ni períodos.

**Gate F3:** prueba E2E de venta y compra con concurrencia, sin stock negativo
indebido, con saldo por cobrar/pagar consistente y kardex reconciliable.

## Fase 4 — Fiscal, tesorería y contabilidad

**Objetivo:** obtener trazabilidad suficiente para operar documentos y cierres
con soporte verificable.

### Facturación electrónica mediante proveedor externo

- [ ] Formalizar el adaptador CPE con estados `draft`, `validated`, `reserved`,
      `submitted`, `accepted`, `observed`, `rejected` y `unknown`.
- [x] Reservar serie/correlativo de forma idempotente y solo una vez.
- [ ] Conservar XML, CDR, PDF, hashes, respuestas, timestamps y correlación del
      proveedor en almacenamiento privado con retención definida.
- [ ] Implementar reconsulta y conciliación ante timeout o respuesta incierta;
      nunca reemitir automáticamente por desconocimiento del estado.
- [ ] Implementar notas de crédito/débito con vínculos, motivos y restricciones
      legales aplicables.
- [ ] Crear fixtures y simuladores para aceptación, rechazo, timeout, respuesta
      duplicada, certificado vencido y errores de validación.

### Tesorería

- [ ] Importar extractos bancarios y configurar matching por referencia, monto,
      fecha y tolerancia.
- [ ] Tratar pagos inciertos, duplicados y sin referencia mediante cola de
      excepción, no marcándolos como pagados.
- [ ] Añadir previsión de flujo de caja, cobranza, mora, compromisos y alertas.
- [ ] Requerir aprobaciones/SoD por importe, beneficiario nuevo y tipo de pago.

### Contabilidad

- [ ] Garantizar en base de datos que un asiento publicado cumple
      `debe = haber` y tiene documento fuente.
- [ ] Impedir editar asientos publicados; implementar reversión mediante nuevos
      asientos trazables.
- [ ] Bloquear períodos cerrados, definir reapertura autorizada y registrar la
      evidencia correspondiente.
- [ ] Completar mapeo PCGE/NIIF, dimensiones, moneda/tipo de cambio y reglas de
      contabilización por evento de negocio.
- [ ] Conciliar diariamente submódulos ↔ libro mayor y producir libro diario,
      mayor, balance de comprobación y cierre mensual reproducible.

**Gate F4:** un mes de prueba se puede cerrar, restaurar y reconstruir sin
diferencias inexplicadas entre CPE, ventas, compras, bancos, inventario y GL.

## Fase 5 — Aprobaciones, documentos y firma

**Objetivo:** elevar los flujos actuales a nivel documental y de control
interno.

- [ ] Configurar matrices de aprobación por módulo, importe, área, entidad
      legal, almacén, centro de costo y riesgo.
- [ ] Añadir delegación temporal, escalamiento, SLA, recordatorios y sustituto
      de aprobador con auditoría.
- [ ] Generar documentos de solicitud con contenido inmutable, versión, hash,
      anexos y estado de firma.
- [ ] Completar la interfaz de verificación de evidencias y firmas internas.
- [ ] Integrar proveedor de certificado/firma cualificada cuando se seleccione;
      conservar evidencia criptográfica, cadena de certificado, sellado de
      tiempo y verificación externa según corresponda.
- [ ] Etiquetar siempre la firma actual como **interna, no cualificada** hasta
      que exista la integración legal.

**Gate F5:** ningún ajuste, compra de riesgo o pago sensible puede ejecutarse
sin política, aprobador autorizado, documento inmutable y evidencia verificable.

## Fase 6 — Plataforma operable y segura

**Objetivo:** poder sostener un piloto real sin depender de intervención manual
constante.

- [ ] Separar staging y producción; usar configuración/secretos independientes.
- [ ] Incorporar worker y colas separadas para CPE, correo, reportes,
      conciliación, importación e integraciones.
- [ ] Definir cuotas por tenant para importación, reportes, API y almacenamiento.
- [ ] Implementar logs estructurados, trace/correlation ID, métricas de negocio
      y alertas sin exponer PII ni contenido fiscal.
- [ ] Definir SLI/SLO para API, reservas, CPE, pagos, GL y respaldos.
- [ ] Definir RPO/RTO, respaldos cifrados, restauración PITR y realizar pruebas
      periódicas de restauración.
- [ ] Crear runbooks para CPE caído, pago incierto, stock descuadrado, degradación
      de BD, fuga de tenant e incidente de seguridad.
- [ ] Fortalecer CI/CD: lint, tipos, pruebas de migración, SAST, SCA, secret
      scanning, SBOM, smoke test y despliegue gradual.

**Gate F6:** restauración probada, alertas activas, jobs reintentables con DLQ,
SLO acordados y despliegue reversible.

## Fase 7 — Reporting, IA y escala SaaS

**Objetivo:** mejorar decisión y productividad sin comprometer el núcleo.

- [ ] Definir diccionario oficial para KPI: facturado, cobrado, neto, utilidad,
      stock disponible, vencido y flujo de caja.
- [ ] Asegurar filtros explícitos de entidad legal, período, moneda y estado en
      reportes; prohibir sumar monedas incompatibles.
- [ ] Ejecutar reportes/exportaciones pesadas de forma asíncrona, con permisos,
      expiración y auditoría de acceso a PII.
- [ ] Mantener RAG con sincronización durable desde outbox, filtros por tenant,
      rol y módulo, y controles de datos obsoletos.
- [ ] Medir calidad del asistente: respuestas sin evidencia, lag de sincronía,
      accesos rechazados y feedback del usuario.
- [ ] Mantener IA en modo asistido: recomendaciones pueden ser automáticas;
      acciones financieras requieren una persona autorizada.
- [ ] Completar ciclo SaaS: planes, límites, métricas de uso, onboarding,
      offboarding, exportación y retención de datos por tenant.

**Gate F7:** reportes conciliados, IA sin exposición cruzada de información y
límites SaaS aplicados sin poner en riesgo procesos fiscales/financieros.

---

## Pruebas obligatorias por cada fase

- [ ] Unitarias de reglas de dominio.
- [ ] Integración con PostgreSQL, constraints, bloqueos y RLS.
- [ ] Concurrencia: doble reserva, doble pago, doble correlativo y reintentos.
- [ ] Contrato con proveedores externos y escenarios de fallo.
- [ ] E2E por rol y tenant, incluidos móviles y modo oscuro.
- [ ] Seguridad: IDOR/BOLA, permisos, SoD, secretos y cargas masivas.
- [ ] Carga, recuperación y conciliación posterior a fallos.
- [ ] UAT con usuarios de ventas, compras, almacén, tesorería y contabilidad.

## Criterio de priorización diario

1. Corregir primero cualquier fuga entre tenants, duplicación de dinero/stock,
   error contable o emisión fiscal incierta.
2. Después resolver los procesos incompletos de la vertical comercial.
3. Luego mejorar automatización, reportes y experiencia de usuario.
4. Solo al final añadir módulos sectoriales: RR. HH., nómina, manufactura/MRP,
   consolidación o integraciones bancarias avanzadas.

## Decisiones explícitamente diferidas

- Facturación electrónica nativa: se conserva el adaptador de tercero.
- Firma/certificado con validez legal: pendiente de selección del proveedor.
- RR. HH., nómina, manufactura, MRP y consolidación: verticales futuros según
  el sector y el cliente piloto.
- Microservicios: no son prioritarios; el monolito modular evoluciona primero
  con contratos, outbox, workers y límites claros.
