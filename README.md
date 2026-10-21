# ERP EnterpriseCloud Backend

API REST multiempresa para ERP EnterpriseCloud, construida con FastAPI, PostgreSQL/Supabase y JWT de Supabase Auth.

## Inicio local

1. Copia `.env.example` como `.env` y completa las variables.
2. Instala dependencias: `py -m pip install -r requirements.txt`.
3. Ejecuta la migración SQL `alembic/versions/0001_initial_schema.sql` en el SQL Editor de Supabase o con `psql`.
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

## Consola de proveedor

La ruta `/oreo` es la entrada de la consola de gestión global de empresas. No es una medida de seguridad por sí sola: la API solo entrega información global cuando el JWT verificado corresponde a un correo incluido en `PLATFORM_ADMIN_EMAILS` de Render. Configura ese valor como una lista separada por comas y mantenlo fuera de Vercel, del repositorio y de los metadatos editables del usuario.

## Operación, monitoreo y recuperación

- Configura Render para supervisar `/ready`: devuelve `200` solo si la API puede conectarse a PostgreSQL; un error de base de datos devuelve `503`.
- Revisa los logs de Render después de cada despliegue y usa `/health` para comprobar que el proceso está vivo, y `/ready` para comprobar que está listo para recibir tráfico.
- Antes de operar con datos reales, establece en Supabase una política de respaldos compatible con tu plan. Conserva copias lógicas cifradas fuera de la aplicación y prueba una restauración en una base de datos de prueba de forma periódica.
- Documenta y ensaya la recuperación: crear una base de prueba, restaurar el respaldo, verificar los tenants, usuarios, ventas e inventario y sólo entonces autorizar una recuperación de producción.
- Las credenciales de base de datos, claves de servicio y respaldos no se guardan en el repositorio ni en variables públicas del frontend.
