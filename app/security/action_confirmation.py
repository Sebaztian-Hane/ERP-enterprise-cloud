"""Fresh, request-bound MFA with durable single-use consumption.

Supabase verifies the OTP in the browser and signs its AMR timestamp. The ERP
accepts that evidence only after issuing a challenge for this exact command.
Consumption shares the business transaction and uses the existing durable
idempotency store; no OTP, TOTP secret or bearer token is persisted.
"""
import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from uuid import UUID, NAMESPACE_URL, uuid5

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.config import get_settings
from app.db.session import get_db
from app.security.auth import decode_access_token, ensure_token_not_revoked, get_actor, platform_actor_from_claims

TTL_SECONDS = 300
OPERATION = "security.action_confirmation"
RULES = [
    (r"/(?:tenants|platform/tenants)(?:/.*)?", "Administrar empresas, accesos o configuración"),
    (r"/legal-entities(?:/.*)?", "Modificar la organización fiscal"),
    (r"/migration-projects/[^/]+/commit", "Cargar definitivamente el grupo de migración"),
    (r"/imports/[^/]+/commit", "Cargar definitivamente la plantilla"),
    (r"/enterprise/records/[^/]+/transition", "Confirmar el cambio de estado del documento"),
    (r"/operations/approvals/[^/]+/(?:approve|reject)", "Firmar la decisión de aprobación"),
    (r"/operations/approvals/(?:policies|delegations)(?:/.*)?", "Modificar las reglas o delegaciones de aprobación"),
    (r"/operations/approvals/documents/[^/]+/attachments", "Adjuntar evidencia a la aprobación"),
    (r"/operations/configuration/.*", "Modificar la configuración de la empresa"),
    (r"/operations/inventory/counts/[^/]+/apply", "Aplicar el conteo de inventario"),
    (r"/inventory/(?:adjustments/[^/]+/execute|transfers/[^/]+/(?:dispatch|receive)|(?:lots|serials)/receive)", "Confirmar movimientos de inventario"),
    (r"/sales(?:/pos-checkout|/[^/]+/(?:cancel|return|electronic-document(?:/void)?))?", "Confirmar la operación de venta o facturación"),
    (r"/purchases(?:/[^/]+/(?:cancel|return))?", "Confirmar la operación de compra"),
    (r"/payments(?:/pos-methods|/[^/]+/execute)?", "Confirmar pagos o sus medios habilitados"),
    (r"/treasury/(?:accounts|movements|petty-cash/funds|petty-cash/expenses/[^/]+/post|petty-cash/replenishments/[^/]+/execute)", "Confirmar la operación de tesorería"),
    (r"/reconciliation/statement-lines/[^/]+/match/[^/]+", "Confirmar la conciliación bancaria"),
    (r"/(?:quotes|sales-orders|purchase-orders)/[^/]+/(?:status|to-sale|to-purchase)", "Confirmar el documento comercial"),
    (r"/sales-deliveries/[^/]+/(?:dispatch|deliver|cancel)", "Confirmar el despacho o entrega"),
]


def protected_action(method, path):
    if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}: return None
    path = path.removeprefix("/api/v1").rstrip("/")
    return next((label for pattern, label in RULES if re.fullmatch(pattern, path)), None)


@dataclass
class ConfirmationIdentity:
    user_id: UUID
    tenant_id: UUID | None
    session_id: str
    aal: str
    verified_at: int


def confirmation_identity(request, db):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "): raise HTTPException(401, "Inicia sesión para confirmar la operación")
    claims = decode_access_token(authorization[7:])
    try:
        user_id = UUID(claims["sub"])
        session_id = str(UUID(claims["session_id"]))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(401, "Inicia sesión nuevamente para verificar tu identidad")
    path = request.url.path.removeprefix("/api/v1").rstrip("/")
    if path.startswith("/platform/"):
        platform_actor_from_claims(claims)
        tenant_id = UUID(request.path_params["tenant_id"]) if "tenant_id" in request.path_params else None
    elif path == "/tenants" and request.method == "POST":
        tenant_id = None  # Consumed by the creation command once its tenant exists.
    else:
        try: tenant_id = UUID(request.headers.get("X-Tenant-ID", ""))
        except ValueError: raise HTTPException(422, "Selecciona la empresa de la operación")
        get_actor(authorization, tenant_id, db)
    ensure_token_not_revoked(db, user_id, claims)
    methods = claims.get("amr")
    timestamps = [item["timestamp"] for item in methods if isinstance(item, dict) and item.get("method") in {"totp", "mfa/totp", "mfa/phone", "mfa/webauthn"} and type(item.get("timestamp")) is int] if isinstance(methods, list) else []
    return ConfirmationIdentity(user_id, tenant_id, session_id, claims.get("aal", "aal1"), max(timestamps, default=0))


def cipher():
    key = get_settings().credential_encryption_key
    if not key: raise HTTPException(503, "La confirmación segura no está configurada en el servidor")
    try:
        root = base64.urlsafe_b64decode(key)
        if len(root) != 32: raise ValueError()
        # Domain separation from encrypted third-party credentials.
        return Fernet(base64.urlsafe_b64encode(hmac.new(root, b"ERP.action-confirmation.v1", hashlib.sha256).digest()))
    except (ValueError, TypeError):
        raise HTTPException(503, "La confirmación segura no está configurada en el servidor")


async def command_hash(request):
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        fields = []
        for key, value in (await request.form()).multi_items():
            if isinstance(value, UploadFile):
                content = await value.read(20 * 1024 * 1024 + 1)
                await value.seek(0)
                if len(content) > 20 * 1024 * 1024: raise HTTPException(413, "Archivo demasiado grande para confirmar")
                value = {"filename": value.filename, "sha256": hashlib.sha256(content).hexdigest()}
            fields.append([key, value])
        body = json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()
    else:
        body = await request.body()
    scope = "\n".join([request.method, request.url.path.rstrip("/"), request.url.query, request.headers.get("Idempotency-Key", "")]).encode()
    return hashlib.sha256(scope + b"\n" + body).hexdigest()


def challenge(identity, digest, label, message="Confirma esta operación con un código nuevo de tu autenticador"):
    payload = {"purpose": OPERATION, "user": str(identity.user_id), "tenant": str(identity.tenant_id), "session": identity.session_id, "hash": digest, "issued": int(time.time())}
    token = cipher().encrypt(json.dumps(payload).encode()).decode()
    raise HTTPException(403, {"code": "ACTION_CONFIRMATION_REQUIRED", "message": message, "action": label, "challenge": token, "expires_in": TTL_SECONDS})


def validate_proof(identity, digest, token):
    try:
        payload = json.loads(cipher().decrypt(token.encode(), ttl=TTL_SECONDS))
        expected = {"purpose": OPERATION, "user": str(identity.user_id), "tenant": str(identity.tenant_id), "session": identity.session_id, "hash": digest}
        if any(payload.get(key) != value for key, value in expected.items()): return False
        issued = payload["issued"]
        return type(issued) is int and identity.aal == "aal2" and issued < identity.verified_at <= int(time.time()) + 30
    except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
        return False


def consume_confirmation(db, identity, digest, tenant_id):
    # A global primary key consumes this signed MFA event once, across tenants
    # and concurrent API instances. A refreshed JWT preserves the same event.
    event_id = uuid5(NAMESPACE_URL, f"{OPERATION}:{identity.user_id}:{identity.session_id}:{identity.verified_at}")
    inserted = db.execute(text("""
        insert into idempotency_requests
          (id, tenant_id, actor_user_id, operation, idempotency_key, request_hash,
           status, response_status, response_body, completed_at)
        values (:id, :tenant, :actor, :operation, :key, :digest, 'completed', 204, '{}', CURRENT_TIMESTAMP)
        on conflict do nothing returning id
    """), {"id": str(event_id), "tenant": str(tenant_id), "actor": str(identity.user_id), "operation": OPERATION, "key": str(event_id), "digest": digest}).scalar_one_or_none()
    return inserted is not None


async def enforce_action_confirmation(request: Request, db: Session = Depends(get_db)):
    label = protected_action(request.method, request.url.path)
    if not label: return
    identity = confirmation_identity(request, db)
    digest = await command_hash(request)
    token = request.headers.get("X-Action-Confirmation", "")
    if not token or not validate_proof(identity, digest, token): challenge(identity, digest, label)
    if identity.tenant_id is None:
        request.state.pending_confirmation = (identity, digest, label)
    elif not consume_confirmation(db, identity, digest, identity.tenant_id):
        challenge(identity, digest, label, "Esta verificación ya se utilizó. Ingresa un código nuevo para esta operación")


def consume_new_tenant_confirmation(request, db, tenant_id):
    identity, digest, label = request.state.pending_confirmation
    if not consume_confirmation(db, identity, digest, tenant_id):
        challenge(identity, digest, label, "Esta verificación ya se utilizó. Ingresa un código nuevo")
