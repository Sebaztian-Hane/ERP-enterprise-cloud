# ERP EnterpriseCloud Backend

API REST multiempresa para ERP EnterpriseCloud, construida con FastAPI, PostgreSQL/Supabase y JWT de Supabase Auth.

## Inicio local

1. Copia `.env.example` como `.env` y completa las variables.
2. Instala dependencias: `py -m pip install -r requirements.txt`.
3. Ejecuta **todas** las migraciones SQL de `alembic/versions/` en orden
   numérico, desde `0001_initial_schema.sql` hasta la última disponible. Una
   instalación nueva que ejecute solo la migración inicial quedará sin módulos,
   políticas RLS y controles incorporados posteriormente.
4. Inicia el servidor: `py -m uvicorn app.main:app --reload`.

La API queda disponible en `/docs`; el chequeo básico en `/health` y el de disponibilidad con base de datos en `/ready`. Los recursos disponibles inicialmente son clientes, proveedores, categorías, productos, almacenes, inventario, compras, ventas, pagos, documentos y dashboard.

## Pruebas

Ejecuta las pruebas con el entorno virtual del proyecto:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

GitHub Actions ejecuta esta verificación en cada cambio enviado a `main` y en cada pull request.

## Contrato de autenticación

Envía `Authorization: Bearer <access_token_de_supabase>` y `X-Tenant-ID: <uuid>` en las rutas de negocio. El tenant nunca se toma del cuerpo de la solicitud. El servidor verifica que el usuario tenga una membresía activa y que su rol tenga el permiso necesario.

## Despliegue en Render

Usa el Dockerfile, define el comando por defecto y configura `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_JWT_ISSUER`, `SUPABASE_JWKS_URL` (o `SUPABASE_JWT_SECRET`) y `CORS_ORIGINS` como variables privadas. No agregues una service-role key al frontend.

Para la carga de documentos, añade también `SUPABASE_SERVICE_ROLE_KEY` solo en Render. La API usa esta clave internamente para escribir en el bucket privado `erp-documents`; no debe enviarse al navegador.

## Idempotencia de comandos críticos

Las rutas `POST /api/v1/sales`, `POST /api/v1/purchases`,
`POST /api/v1/payments`, `POST /api/v1/inventory/adjustments` y
  `POST /api/v1/inventory/transfers`, los commits de importación y la emisión
  de comprobantes electrónicos, además de reservas, conteos físicos y sus
  transiciones de transferencia, aceptan opcionalmente el encabezado
`Idempotency-Key`. El cliente debe enviar un UUID u otro valor aleatorio estable
por intento de operación y reutilizarlo únicamente si necesita reintentar
exactamente el mismo payload después de un timeout o pérdida de red.

- La misma clave, usuario, empresa y payload devuelve el resultado original sin
  repetir la venta ni el pago.
- Reutilizar la clave con datos diferentes devuelve `409 Conflict`.
- Las claves se persisten junto con el comando y no se exponen por la Data API.

La emisión frente a APISUNAT conserva este contrato para los reintentos del
navegador. La entrega durable al proveedor se implementará como un consumidor
de outbox: un proveedor externo puede confirmar el documento y cortar la
conexión antes de que el servidor reciba su respuesta, caso que no puede
resolverse de forma segura con un reintento HTTP directo.

## Outbox y sincronización RAG

Los cambios que alimentan al asistente se guardan primero en la tabla
`outbox_events`, dentro de la misma transacción que el dato empresarial. El
proceso `python -m app.workers.outbox` entrega posteriormente los eventos a
n8n, con reintentos acotados y estado `dead` si no logra entregarlos.

En Render Free, el API inicia este consumidor en un hilo supervisado para no
requerir un segundo servicio de pago. El outbox persiste en PostgreSQL: un
reinicio solo retrasa una entrega y las leases expiradas se recuperan. Si se
migra a un **Background Worker** separado, establece
`OUTBOX_EMBEDDED_WORKER_ENABLED=false` en el API y usa el mismo comando con
`DATABASE_URL` y secretos `RAG_*`. No habilites ningún consumidor sin ejecutar
antes la migración `0035_transactional_outbox.sql`.

## Consola de proveedor

La ruta `/oreo` es la entrada de la consola de gestión global de empresas. No es una medida de seguridad por sí sola: la API solo entrega información global cuando el JWT verificado corresponde a un correo incluido en `PLATFORM_ADMIN_EMAILS` de Render. Configura ese valor como una lista separada por comas y mantenlo fuera de Vercel, del repositorio y de los metadatos editables del usuario.

## Operación, monitoreo y recuperación

- Configura Render para supervisar `/ready`: devuelve `200` solo si la API puede conectarse a PostgreSQL; un error de base de datos devuelve `503`.
- Revisa los logs de Render después de cada despliegue y usa `/health` para comprobar que el proceso está vivo, y `/ready` para comprobar que está listo para recibir tráfico.
- Antes de operar con datos reales, establece en Supabase una política de respaldos compatible con tu plan. Conserva copias lógicas cifradas fuera de la aplicación y prueba una restauración en una base de datos de prueba de forma periódica.
- Documenta y ensaya la recuperación: crear una base de prueba, restaurar el respaldo, verificar los tenants, usuarios, ventas e inventario y sólo entonces autorizar una recuperación de producción.
- Las credenciales de base de datos, claves de servicio y respaldos no se guardan en el repositorio ni en variables públicas del frontend.
