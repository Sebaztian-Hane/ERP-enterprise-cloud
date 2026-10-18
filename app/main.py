import logging
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
app = FastAPI(title="ERP EnterpriseCloud API", version="0.1.0", docs_url="/docs", openapi_url="/openapi.json")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    # The API only exposes read, create and partial-update endpoints.  Keeping
    # this list explicit prevents browsers from preflighting unused verbs.
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"],
)
app.include_router(api_router)

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
