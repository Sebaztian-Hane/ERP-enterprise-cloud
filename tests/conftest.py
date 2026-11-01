import os


# Unit tests must remain isolated after a developer configures a production
# .env. Settings prioritizes these process values over that private file.
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": "sqlite+pysqlite:///:memory:",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_JWT_ISSUER": "https://example.supabase.co/auth/v1",
    "SUPABASE_JWT_SECRET": "test-only-secret",
    "SUPABASE_JWKS_URL": "",
    "SUPABASE_SERVICE_ROLE_KEY": "",
    "CORS_ORIGINS": "http://localhost:5173",
    "OUTBOX_EMBEDDED_WORKER_ENABLED": "false",
})
for name in (
    "BREVO_API_KEY", "DECOLECTA_API_KEY", "GEMINI_API_KEY", "CREDENTIAL_ENCRYPTION_KEY",
    "APISUNAT_PERSONA_ID", "APISUNAT_PERSONA_TOKEN", "PLATFORM_ADMIN_EMAILS",
    "RAG_INTEGRATION_KEY", "RAG_CHAT_WEBHOOK_SECRET", "RAG_SYNC_WEBHOOK_SECRET",
):
    os.environ[name] = ""
# Optional URLs must use valid values when present; do not substitute an empty
# string for AnyHttpUrl fields in Settings. These test domains are never called.
os.environ["RAG_CHAT_WEBHOOK_URL"] = "https://example.test/disabled"
os.environ["RAG_SYNC_WEBHOOK_URL"] = "https://example.test/disabled"
