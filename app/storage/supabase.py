from urllib.parse import quote
import httpx
from fastapi import HTTPException, UploadFile
from app.core.config import get_settings


def _headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="Storage no está configurado en el servidor")
    return {"Authorization": f"Bearer {settings.supabase_service_role_key}", "apikey": settings.supabase_service_role_key}


async def upload(path: str, file: UploadFile, content: bytes) -> None:
    settings = get_settings()
    headers = _headers() | {"Content-Type": file.content_type or "application/octet-stream", "x-upsert": "false"}
    url = f"{str(settings.supabase_url).rstrip('/')}/storage/v1/object/{settings.documents_bucket}/{quote(path, safe='/')}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, content=content)
    if response.is_error:
        raise HTTPException(status_code=502, detail="No se pudo guardar el documento")


def upload_bytes(path: str, content: bytes, *, content_type: str) -> None:
    """Store server-generated evidence without creating a public object."""
    settings = get_settings()
    headers = _headers() | {"Content-Type": content_type, "x-upsert": "false"}
    url = f"{str(settings.supabase_url).rstrip('/')}/storage/v1/object/{settings.documents_bucket}/{quote(path, safe='/')}"
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, content=content)
    except httpx.HTTPError as exc:
        raise RuntimeError("No se pudo guardar la evidencia fiscal") from exc
    if response.is_error:
        raise RuntimeError("No se pudo guardar la evidencia fiscal")


async def signed_url(path: str, expires_in: int = 300) -> str:
    settings = get_settings()
    url = f"{str(settings.supabase_url).rstrip('/')}/storage/v1/object/sign/{settings.documents_bucket}/{quote(path, safe='/')}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, headers=_headers(), json={"expiresIn": expires_in})
    if response.is_error:
        raise HTTPException(status_code=502, detail="No se pudo generar la URL de descarga")
    signed_path = response.json().get("signedURL")
    if not signed_path:
        raise HTTPException(status_code=502, detail="Storage devolvió una URL inválida")
    return signed_path if signed_path.startswith("http") else f"{str(settings.supabase_url).rstrip('/')}/storage/v1{signed_path}"
