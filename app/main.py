import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from app.api.router import api_router
from app.core.config import get_settings
from app.db.session import engine
from app.core.rate_limit import limiter

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Run the durable RAG outbox on Free Render without a second service.

    The transactional outbox remains in PostgreSQL, so an API restart only
    delays a delivery. A paid Render worker can later replace this fallback by
    setting ``OUTBOX_EMBEDDED_WORKER_ENABLED=false`` on the API.
    """
    stop_event = worker_thread = None
    has_rag_target = bool(settings.rag_sync_webhook_url and settings.rag_sync_webhook_secret)
    # A per-entity CPE credential is resolved when an outbox event is handled.
    # The worker therefore needs only the server encryption key to start.
    has_cpe_target = bool(settings.credential_encryption_key or (settings.apisunat_persona_id and settings.apisunat_persona_token))
    if settings.outbox_embedded_worker_enabled and (has_rag_target or has_cpe_target):
        from app.workers.outbox import start_embedded_worker

        stop_event, worker_thread = start_embedded_worker()
        logging.info("embedded_outbox_worker_enabled")
    yield
    if stop_event and worker_thread:
        stop_event.set()
        worker_thread.join(timeout=5)


app = FastAPI(
    title="ERP EnterpriseCloud API",
    version="0.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.middleware("http")
async def request_trace(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        logging.exception("unhandled_request_error path=%s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Error interno del servidor"})
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # API endpoints return JSON only.  Do not set this on the interactive API
    # documentation because Swagger UI needs to load its own assets.
    if request.url.path not in {"/docs", "/redoc", "/openapi.json"}:
        response.headers["Content-Security-Policy"] = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    logging.info("request method=%s path=%s status=%s", request.method, request.url.path, response.status_code)
    return response


# Register CORS after the trace middleware so it is outermost.  This keeps the
# allow-origin header on JSON error responses produced by request_trace; without
# it browsers hide a useful API error behind a misleading CORS failure.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    # The API only exposes read, create and partial-update endpoints.  Keeping
    # this list explicit prevents browsers from preflighting unused verbs.
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    # Browser clients must be allowed to send the replay key on cross-origin
    # POST requests. Omitting it makes the browser reject the preflight before
    # any command reaches the idempotency layer.
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID", "Idempotency-Key"],
)
app.include_router(api_router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "environment": settings.app_env}


@app.get("/ready", tags=["system"])
def readiness():
    """Indica si la API puede atender tráfico con su dependencia principal."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logging.exception("readiness_database_error")
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "dependency": "database"},
        )
    return {"status": "ok", "dependency": "database"}
