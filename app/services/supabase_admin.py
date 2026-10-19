from uuid import UUID
import httpx
from fastapi import HTTPException
from app.core.config import Settings


def _headers(settings: Settings) -> dict[str, str]:
    if not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="El servicio de autenticación no está configurado")
    return {"Authorization": f"Bearer {settings.supabase_service_role_key}", "apikey": settings.supabase_service_role_key}


async def _request(settings: Settings, method: str, path: str, *, body: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(method, f"{str(settings.supabase_url).rstrip('/')}{path}", headers=_headers(settings), json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="No fue posible contactar el servicio de autenticación") from exc
    if response.status_code >= 400:
        detail = response.json().get("msg") if response.headers.get("content-type", "").startswith("application/json") else None
        raise HTTPException(status_code=409 if response.status_code in {400, 409, 422} else 502, detail=detail or "No fue posible completar la operación de acceso")
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="El servicio de autenticación devolvió una respuesta no válida") from exc


async def create_user(settings: Settings, *, email: str, temporary_password: str) -> UUID:
    payload = await _request(settings, "POST", "/auth/v1/admin/users", body={
        "email": email,
        "password": temporary_password,
        "email_confirm": True,
        "app_metadata": {"force_password_change": True},
    })
    try:
        return UUID(payload["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="No se recibió un usuario válido al crear el acceso") from exc


async def get_user_email(settings: Settings, user_id: UUID) -> str:
    payload = await _request(settings, "GET", f"/auth/v1/admin/users/{user_id}")
    email = payload.get("email")
    if not isinstance(email, str) or not email:
        raise HTTPException(status_code=502, detail="No se encontró un correo válido para esta cuenta")
    return email


async def update_password(settings: Settings, *, user_id: UUID, password: str) -> None:
    await _request(settings, "PUT", f"/auth/v1/admin/users/{user_id}", body={
        "password": password,
        "app_metadata": {"force_password_change": False},
    })
