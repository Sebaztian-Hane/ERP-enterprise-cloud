import base64
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.services.internal_signatures import jwk_fingerprint, signing_message, verify_p256_signature


def _public_jwk(key):
    numbers = key.public_key().public_numbers()
    encode = lambda value: base64.urlsafe_b64encode(value.to_bytes(32, "big")).decode().rstrip("=")
    return {"kty": "EC", "crv": "P-256", "x": encode(numbers.x), "y": encode(numbers.y)}


def _webcrypto_signature(key, message):
    der = key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


def test_internal_signature_verifies_exact_bound_message():
    private = ec.generate_private_key(ec.SECP256R1())
    message = signing_message(challenge_id=uuid4(), document_id=uuid4(), document_hash="a" * 64, decision="approved", signer_user_id=uuid4())
    verify_p256_signature(public_jwk=_public_jwk(private), message=message, signature_b64=_webcrypto_signature(private, message))


def test_internal_signature_cannot_be_reused_for_a_different_decision():
    private = ec.generate_private_key(ec.SECP256R1())
    message = signing_message(challenge_id=uuid4(), document_id=uuid4(), document_hash="a" * 64, decision="approved", signer_user_id=uuid4())
    signature = _webcrypto_signature(private, message)
    with pytest.raises(ValueError):
        verify_p256_signature(public_jwk=_public_jwk(private), message=message.replace("approved", "rejected"), signature_b64=signature)


def test_jwk_fingerprint_is_stable():
    key = ec.generate_private_key(ec.SECP256R1())
    assert jwk_fingerprint(_public_jwk(key)) == jwk_fingerprint(dict(reversed(list(_public_jwk(key).items()))))
