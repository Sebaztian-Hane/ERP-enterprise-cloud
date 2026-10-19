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
    invite_redirect_url: str | None = None
    max_document_size_bytes: int = 10_485_760
    cors_origins: str = "http://localhost:5173"
    # RAG is intentionally decoupled from the transactional Supabase project.
    # These values authenticate only the ERP <> n8n integration.
    rag_integration_key: str | None = Field(default=None, repr=False)
    rag_sync_webhook_url: AnyHttpUrl | None = None
    rag_sync_webhook_secret: str | None = Field(default=None, repr=False)
    rag_chat_webhook_url: AnyHttpUrl | None = None
    rag_chat_webhook_secret: str | None = Field(default=None, repr=False)
    # APISUNAT stays entirely server-side. The development token is a Render
    # secret and is never exposed through the browser or persisted in SQL.
    apisunat_base_url: AnyHttpUrl = "https://back.apisunat.com"
    apisunat_persona_id: str | None = None
    apisunat_persona_token: str | None = Field(default=None, repr=False)
    apisunat_issuer_ruc: str | None = None
    apisunat_issuer_trade_name: str | None = None
    apisunat_issuer_legal_name: str | None = None
    apisunat_issuer_address: str | None = None
    apisunat_invoice_series: str = "F001"
    apisunat_receipt_series: str = "B001"
    # DeColecta is used only to prefill customer data from DNI/RUC during sales.
    decolecta_base_url: AnyHttpUrl = "https://api.decolecta.com"
    decolecta_api_key: str | None = Field(default=None, repr=False)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
