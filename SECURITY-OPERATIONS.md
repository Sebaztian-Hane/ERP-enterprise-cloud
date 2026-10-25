# Seguridad y operación — EnterpriseCloud

## Controles aplicados

- La API verifica JWT firmado, membresía activa y `X-Tenant-ID` antes de cada operación con empresa.
- Toda consulta operativa se filtra por `tenant_id`; RLS replica el aislamiento para el acceso directo a Supabase.
- Las credenciales APISUNAT y el estado de revocación de sesiones tienen RLS de denegación explícita: solo la API del servidor puede leerlos o modificarlos.
- Una contraseña cambiada o la acción **Cerrar sesiones en todos los dispositivos** invalida de inmediato los tokens ya emitidos en la API. El cliente borra además su sesión local.
- Acciones mutables relevantes conservan auditoría e idempotencia.

## Rotación de secretos

Los secretos no se rotan desde código ni se colocan en Git. Para una rotación:

1. Genera una nueva clave en el proveedor correspondiente.
2. Actualízala como secreto en Render.
3. Despliega y comprueba `GET /ready`.
4. Revoca la clave anterior en el proveedor solo después de la comprobación.
5. Registra la intervención sin incluir el valor del secreto.

Prioridad de rotación si alguno fue compartido en un canal no seguro:

- `SUPABASE_SERVICE_ROLE_KEY` / secret key de Supabase.
- Tokens de APISUNAT, Decolecta, Brevo, Gemini y n8n.
- `CREDENTIAL_ENCRYPTION_KEY`, claves HMAC de integraciones y claves JWT antiguas.

## Verificación operativa después de un despliegue

1. `GET /ready` debe responder `200` con dependencia de base de datos sana.
2. Ejecutar la suite backend y la compilación frontend.
3. Consultar el asesor de seguridad de Supabase: no deben aparecer tablas expuestas sin RLS o políticas.
4. Probar con dos tenants que un ID de la empresa A no puede leerse, cambiarse ni procesarse desde la empresa B.
5. Confirmar que un cambio de contraseña y el cierre global de sesiones exigen iniciar sesión de nuevo.

## Límites de la plataforma actual

- La protección contra contraseñas filtradas continúa excluida por la decisión de costo ya indicada.
- Backups gestionados, restauración verificada, workers separados y alertas externas requieren configuración de infraestructura en los proveedores; no deben simularse desde la aplicación.
