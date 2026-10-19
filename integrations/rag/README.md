# RAG de ERP EnterpriseCloud con n8n, Gemini, DeepSeek y Supabase Vector

Esta integración tiene dos flujos independientes:

1. `workflow-sync-erp-vectors.json`: recibe eventos del ERP y hace una reindexación programada de respaldo. Obtiene datos operativos normalizados, genera embeddings con Gemini y los guarda en el proyecto vectorial.
2. `workflow-chat-erp-rag.json`: recibe consultas firmadas por el backend, busca contexto solamente del `tenant_id` del usuario y responde mediante DeepSeek.

## Antes de importar

1. Crea el nuevo proyecto de Supabase destinado exclusivamente al RAG.
2. En su SQL Editor, ejecuta `supabase-vector.sql` completo.
3. En el VPS de n8n define estas variables de entorno y reinicia n8n:

```env
ERP_API_URL=https://erp-enterprisecloud-api.onrender.com/api/v1
ERP_RAG_INTEGRATION_KEY=<secreto-aleatorio-de-32-o-mas-caracteres>
ERP_RAG_WEBHOOK_SECRET=<secreto-aleatorio-distinto-de-32-o-mas-caracteres>
SUPABASE_RAG_URL=https://<nuevo-proyecto>.supabase.co
SUPABASE_RAG_SERVICE_ROLE_KEY=<service_role-del-nuevo-proyecto>
GEMINI_API_KEY=<clave-de-Gemini>
GEMINI_EMBEDDING_MODEL=gemini-embedding-2
DEEPSEEK_API_KEY=<clave-de-DeepSeek>
DEEPSEEK_CHAT_MODEL=deepseek-chat
NODE_FUNCTION_ALLOW_BUILTIN=crypto
```

`NODE_FUNCTION_ALLOW_BUILTIN=crypto` permite a los nodos Code verificar el HMAC de los webhooks. No expongas ninguna de estas variables en Vercel ni en el frontend.

4. Importa ambos JSON en n8n y actívalos. Copia las URLs de producción de los nodos Webhook.
5. En Render configura, con los mismos secretos:

```env
RAG_INTEGRATION_KEY=<ERP_RAG_INTEGRATION_KEY>
RAG_SYNC_WEBHOOK_URL=https://<tu-n8n>/webhook/erp-rag-sync
RAG_SYNC_WEBHOOK_SECRET=<ERP_RAG_WEBHOOK_SECRET>
RAG_CHAT_WEBHOOK_URL=https://<tu-n8n>/webhook/erp-rag-chat
RAG_CHAT_WEBHOOK_SECRET=<ERP_RAG_WEBHOOK_SECRET>
```

## Seguridad y alcance

- La API de exportación requiere `X-RAG-Integration-Key` y no incluye correos, RUC/DNI, contraseñas, archivos ni identidades de usuarios.
- Cada documento vectorial contiene un `tenant_id`; la función de búsqueda lo exige como filtro, por lo que no hay búsqueda entre empresas.
- El frontend no conoce ni la URL privada de n8n ni credenciales de Gemini, DeepSeek o Supabase. Usará `POST /api/v1/rag/chat`, autenticado con el JWT actual del ERP.
- La memoria de conversación se guarda en `rag_chat_messages`, segregada por empresa, usuario y conversación. Se conservan solo los últimos diez mensajes como contexto.
- Los cambios operativos generan un evento solo después del `commit`; el cron del workflow vuelve a indexar todas las empresas como recuperación ante caídas temporales.

## Primera carga y operación

Ejecuta manualmente `Sincronizar ERP a Supabase Vector` una vez para la carga inicial. Luego quedan activos el webhook por cambios y el cron cada hora. Si cambias el modelo/dimensión de Gemini, crea una migración y re-embebe todo: una búsqueda solo es válida cuando consulta y documentos usan el mismo modelo y dimensión.
