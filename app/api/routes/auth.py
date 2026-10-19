from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from app.core.config import get_settings
from app.core.rate_limit import limiter
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import PasswordChangeCode, Tenant, TenantMembership
from app.schemas.common import Message, PasswordChangeConfirm, PasswordChangeRequest, TenantOut
from app.security.auth import Actor, get_actor, get_user_id
from app.services.audit import record
from app.services.brevo import password_code_html, send_brevo_email
from app.services.supabase_admin import get_user_email, update_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
@limiter.limit("60/minute")
def me(request: Request, response: Response, actor: Actor = Depends(get_actor)) -> Actor:
    return actor


@router.get("/tenants", response_model=list[TenantOut])
@limiter.limit("30/minute")
def available_tenants(request: Request, response: Response, user_id = Depends(get_user_id), db: Session = Depends(get_db)):
    return db.scalars(select(Tenant).join(TenantMembership).where(
        TenantMembership.user_id == user_id, TenantMembership.is_active.is_(True), Tenant.is_active.is_(True)
    ).order_by(Tenant.name)).all()


def code_hash(user_id, code: str) -> str:
    settings = get_settings()
    if not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="El servicio de seguridad no está configurado")
    return hmac.new(settings.supabase_service_role_key.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


@router.post("/password-change/request", response_model=Message)
@limiter.limit("3/15minute")
async def request_password_change(request: Request, response: Response, data: PasswordChangeRequest, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    settings = get_settings()
    email = await get_user_email(settings, actor.user_id)
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(timezone.utc)
    # One active challenge at a time prevents a previously sent code from
    # remaining valid after the user asks for another one.
    db.execute(update(PasswordChangeCode).where(
        PasswordChangeCode.user_id == actor.user_id,
        PasswordChangeCode.used_at.is_(None),
    ).values(used_at=now))
    challenge = PasswordChangeCode(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        code_hash=code_hash(actor.user_id, code),
        expires_at=now + timedelta(minutes=settings.password_change_code_ttl_minutes),
    )
    db.add(challenge)
    db.flush()
    await send_brevo_email(settings, recipient=email, subject="Código para cambiar tu contraseña", html=password_code_html(code=code))
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="request_password_change", module="profile", entity="password_change_code", entity_id=challenge.id)
    db.commit()
    return Message(detail="Enviamos un código de verificación a tu correo.")


@router.post("/password-change/confirm", response_model=Message)
@limiter.limit("5/15minute")
async def confirm_password_change(request: Request, response: Response, data: PasswordChangeConfirm, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    challenge = db.scalar(select(PasswordChangeCode).where(
        PasswordChangeCode.user_id == actor.user_id,
        PasswordChangeCode.used_at.is_(None),
    ).order_by(PasswordChangeCode.created_at.desc()))
    if not challenge or challenge.expires_at < now:
        raise HTTPException(status_code=422, detail="El código venció o no existe. Solicita uno nuevo.")
    if challenge.attempts >= 5:
        challenge.used_at = now
        db.commit()
        raise HTTPException(status_code=429, detail="Se agotaron los intentos. Solicita un nuevo código.")
    if not hmac.compare_digest(challenge.code_hash, code_hash(actor.user_id, data.code)):
        challenge.attempts += 1
        db.commit()
        raise HTTPException(status_code=422, detail="El código no es válido.")
    await update_password(get_settings(), user_id=actor.user_id, password=data.new_password)
    challenge.used_at = now
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="change_password", module="profile", entity="password_change_code", entity_id=challenge.id)
    db.commit()
    return Message(detail="Tu contraseña se actualizó correctamente.")
