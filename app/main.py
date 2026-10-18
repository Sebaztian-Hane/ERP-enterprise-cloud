import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from app.api.router import api_router
from app.core.config import get_settings
from app.core.rate_limit import limiter

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title="ERP EnterpriseCloud API", version="0.1.0", docs_url="/docs", openapi_url="/openapi.json")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"])
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
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    logging.info("request method=%s path=%s status=%s", request.method, request.url.path, response.status_code)
    return response


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "environment": settings.app_env}
