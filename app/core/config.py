from functools import lru_cache
from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    database_url: str
    supabase_url: AnyHttpUrl
    supabase_jwt_issuer: str
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_url: str | None = None
    supabase_jwt_secret: str | None = Field(default=None, repr=False)
    supabase_service_role_key: str | None = Field(default=None, repr=False)
    documents_bucket: str = "erp-documents"
    # Global provider access is deliberately independent from a tenant role.
    # Keep this server-side allowlist in Render; it must never be sent to the
    # browser or be derived from editable user metadata.
    platform_admin_emails: str = Field(default="", repr=False)
    invite_redirect_url: str | None = None
    # Mail is sent only from the API. These values are Render secrets and must
    # never be exposed through the frontend or persisted in the database.
    brevo_api_key: str | None = Field(default=None, repr=False)
    brevo_sender_email: str | None = None
    brevo_sender_name: str = "EnterpriseCloud"
    password_change_code_ttl_minutes: int = 10
    max_document_size_bytes: int = 10_485_760
    cors_origins: str = "http://localhost:5173"
    # RAG is intentionally decoupled from the transactional Supabase project.
    # These values authenticate only the ERP <> n8n integration.
    rag_integration_key: str | None = Field(default=None, repr=False)
    rag_sync_webhook_url: AnyHttpUrl | None = None
    rag_sync_webhook_secret: str | None = Field(default=None, repr=False)
    # Render's free tier does not offer background workers. When enabled, the
    # API process runs the outbox consumer in a daemon thread. PostgreSQL
    # leases still make this safe if the application is later scaled out.
    outbox_embedded_worker_enabled: bool = True
    rag_chat_webhook_url: AnyHttpUrl | None = None
    rag_chat_webhook_secret: str | None = Field(default=None, repr=False)
    # APISUNAT stays entirely server-side. The development token is a Render
    # secret and is never exposed through the browser or persisted in SQL.
    apisunat_base_url: AnyHttpUrl = "https://back.apisunat.com"
    # Exact extra hosts used by the provider to serve CPE artifacts (comma-separated).
    # Empty means only the configured APISUNAT host is trusted, preventing SSRF.
    apisunat_artifact_allowed_hosts: str = ""
    apisunat_persona_id: str | None = None
    apisunat_persona_token: str | None = Field(default=None, repr=False)
    apisunat_issuer_ruc: str | None = None
    apisunat_issuer_trade_name: str | None = None
    apisunat_issuer_legal_name: str | None = None
    apisunat_issuer_address: str | None = None
    apisunat_invoice_series: str = "F001"
    apisunat_receipt_series: str = "B001"
    # Fernet key used exclusively by the API to encrypt each legal entity's
    # APISUNAT token at rest. It is a Render secret, never a browser value.
    credential_encryption_key: str | None = Field(default=None, repr=False)
    # DeColecta is used only to prefill customer data from DNI/RUC during sales.
    decolecta_base_url: AnyHttpUrl = "https://api.decolecta.com"
    decolecta_api_key: str | None = Field(default=None, repr=False)
    # Gemini is used only by the automatic, aggregated business insight.
    # It is a Render secret and never reaches the client.
    gemini_api_key: str | None = Field(default=None, repr=False)
    gemini_insight_model: str = "gemini-flash-lite-latest"
    insight_min_operations: int = 4
    insight_refresh_min_operations: int = 3
    insight_cache_hours: int = 12

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def platform_admin_email_set(self) -> set[str]:
        return {
            email.strip().casefold()
            for email in self.platform_admin_emails.split(",")
            if email.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
