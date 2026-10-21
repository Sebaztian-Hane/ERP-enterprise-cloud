"""Verification for EnterpriseCloud's non-qualified internal digital signature.

Private keys are generated and kept as non-extractable Web Crypto keys in the
browser.  The service receives only a public ECDSA P-256 JWK and verifies a
short-lived, single-use challenge bound to a document, decision and user.
"""

from __future__ import annotations

import base64
from hashlib import sha256
import json
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


PREFIX = "enterprisecloud.internal-signature.v1"


def canonical_jwk(jwk: dict[str, str]) -> str:
    return json.dumps(jwk, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def jwk_fingerprint(jwk: dict[str, str]) -> str:
    return sha256(canonical_jwk(jwk).encode("utf-8")).hexdigest()


def signing_message(*, challenge_id: UUID | str, document_id: UUID | str, document_hash: str, decision: str, signer_user_id: UUID | str) -> str:
    return "|".join((PREFIX, str(challenge_id), str(document_id), document_hash, decision, str(signer_user_id)))


def message_hash(message: str) -> str:
    return sha256(message.encode("utf-8")).hexdigest()


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_p256_jwk(jwk: dict[str, str]) -> None:
    if set(jwk) - {"kty", "crv", "x", "y", "ext", "key_ops"} or jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("La clave pública debe usar ECDSA P-256")
    if not isinstance(jwk.get("x"), str) or not isinstance(jwk.get("y"), str):
        raise ValueError("La clave pública está incompleta")
    if len(_b64url(jwk["x"])) != 32 or len(_b64url(jwk["y"])) != 32:
        raise ValueError("La clave pública no es P-256 válida")


def verify_p256_signature(*, public_jwk: dict[str, str], message: str, signature_b64: str) -> None:
    """Validate the Web Crypto raw R||S ECDSA signature against the exact message."""
    validate_p256_jwk(public_jwk)
    raw_signature = base64.b64decode(signature_b64, validate=True)
    if len(raw_signature) != 64:
        raise ValueError("La firma digital tiene un formato inválido")
    try:
        public_key = ec.EllipticCurvePublicNumbers(
            int.from_bytes(_b64url(public_jwk["x"]), "big"),
            int.from_bytes(_b64url(public_jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
        public_key.verify(
            encode_dss_signature(int.from_bytes(raw_signature[:32], "big"), int.from_bytes(raw_signature[32:], "big")),
            message.encode("utf-8"),
            ec.ECDSA(hashes.SHA256()),
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("La firma digital no coincide con la constancia") from exc
