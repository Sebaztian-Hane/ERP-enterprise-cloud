# ERP EnterpriseCloud Backend

API REST multiempresa para ERP EnterpriseCloud, construida con FastAPI, PostgreSQL/Supabase y JWT de Supabase Auth.

## Inicio local

1. Copia `.env.example` como `.env` y completa las variables.
2. Instala dependencias: `py -m pip install -r requirements.txt`.
3. Ejecuta la migración SQL `alembic/versions/0001_initial_schema.sql` en el SQL Editor de Supabase o con `psql`.
4. Inicia el servidor: `py -m uvicorn app.main:app --reload`.

La API queda disponible en `/docs`; el chequeo de servicio en `/health`. Los recursos disponibles inicialmente son clientes, proveedores, categorías, productos, almacenes, inventario, compras, ventas, pagos, documentos y dashboard.

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
