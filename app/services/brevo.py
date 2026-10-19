from html import escape
import httpx
from fastapi import HTTPException
from app.core.config import Settings


async def send_brevo_email(settings: Settings, *, recipient: str, subject: str, html: str) -> None:
    """Send transactional mail without ever returning secrets or message bodies."""
    if not settings.brevo_api_key or not settings.brevo_sender_email:
        raise HTTPException(status_code=503, detail="El servicio de correo no está configurado")
    payload = {
        "sender": {"email": settings.brevo_sender_email, "name": settings.brevo_sender_name},
        "to": [{"email": recipient}],
        "subject": subject,
        "htmlContent": html,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": settings.brevo_api_key, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="No fue posible contactar el servicio de correo") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="No fue posible enviar el correo de acceso")


def invitation_html(*, temporary_password: str, tenant_name: str, role: str) -> str:
    return (
        "<h2>Acceso a EnterpriseCloud</h2>"
        f"<p>Se te asignó acceso a <strong>{escape(tenant_name)}</strong> con el rol <strong>{escape(role)}</strong>.</p>"
        "<p>Ingresa con este correo y tu contraseña temporal:</p>"
        f"<p style=\"font-size:18px;font-weight:bold;letter-spacing:1px\">{escape(temporary_password)}</p>"
        "<p>Por seguridad, cámbiala desde <strong>Mi perfil</strong> al iniciar sesión.</p>"
    )


def password_code_html(*, code: str) -> str:
    return (
        "<h2>Confirma el cambio de contraseña</h2>"
        "<p>Usa este código de verificación en EnterpriseCloud. Vence en 10 minutos.</p>"
        f"<p style=\"font-size:24px;font-weight:bold;letter-spacing:5px\">{escape(code)}</p>"
        "<p>Si no solicitaste este cambio, ignora este correo.</p>"
    )
