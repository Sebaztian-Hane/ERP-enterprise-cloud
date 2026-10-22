from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.services import apisunat_credentials


def test_entity_token_is_ciphertext_and_round_trips(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        apisunat_credentials,
        "get_settings",
        lambda: SimpleNamespace(credential_encryption_key=key),
    )
    token = "DEV_example_token_that_must_never_be_persisted_plaintext"
    ciphertext = apisunat_credentials.encrypt_persona_token(token)

    assert ciphertext != token
    assert apisunat_credentials.decrypt_persona_token(ciphertext) == token


def test_entity_token_refuses_to_encrypt_without_server_key(monkeypatch):
    monkeypatch.setattr(
        apisunat_credentials,
        "get_settings",
        lambda: SimpleNamespace(credential_encryption_key=None),
    )

    with pytest.raises(HTTPException) as error:
        apisunat_credentials.encrypt_persona_token("DEV_example_token")

    assert error.value.status_code == 503


def test_sales_and_delivery_do_not_read_global_apisunat_token():
    with open("app/api/routes/sales.py", encoding="utf-8") as source:
        sales_source = source.read()
    with open("app/services/electronic_delivery.py", encoding="utf-8") as source:
        delivery_source = source.read()

    assert "settings.apisunat_persona_token" not in sales_source
    assert "settings.apisunat_persona_token" not in delivery_source
