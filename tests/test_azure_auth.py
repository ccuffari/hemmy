"""Test di `utils.azure_auth` — il "choke point" condiviso da ~60 tool nativi
(inclusi molti generati a runtime dal meta-tooling e poi promossi).

Copre il fix di sicurezza critico: PRIMA, `get_arm_token()` usava sempre e
solo `DefaultAzureCredential()` — su un servizio con una Managed Identity
assegnata (o credenziali `az login` residue), TUTTI gli utenti della
piattaforma avrebbero eseguito i tool Azure (inclusi quelli di SCRITTURA:
cancel_run, rerun_pipeline, resources.pause, deploy.rollback, ecc.) con la
STESSA identità condivisa, indipendentemente dal proprio account Azure OAuth.

Ora `get_arm_token()` usa PRIMA la sessione OAuth dell'utente corrente
(thread-local, la stessa di `infra/clients.py._credential()`), e solo se
assente ripiega su `DefaultAzureCredential` (fallback per CLI locale a
singolo operatore, `user_id=None`).
"""

from __future__ import annotations

import threading

import pytest

from hemmy.auth import oauth_providers as op
from hemmy.utils import azure_auth


class _FakeCredential:
    """Credenziale fittizia: il token incorpora l'identità per verificare
    quale sessione è stata effettivamente usata."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def get_token(self, *scopes: str, **kwargs):
        class _Token:
            pass

        t = _Token()
        t.token = f"TOKEN-FOR-{self.tag}"
        t.expires_on = 9999999999
        return t


@pytest.fixture(autouse=True)
def _clean_state():
    op._AZURE_SESSIONS.clear()
    op.clear_current_user()
    yield
    op._AZURE_SESSIONS.clear()
    op.clear_current_user()


def test_get_arm_token_uses_bound_user_oauth_session():
    """Se l'utente corrente (thread-local) ha una sessione Azure OAuth,
    `get_arm_token()` deve usare QUELLA, non DefaultAzureCredential."""
    op._AZURE_SESSIONS["u1"] = _FakeCredential("u1")
    op.set_current_user("u1")
    assert azure_auth.get_arm_token() == "TOKEN-FOR-u1"


def test_get_arm_token_isolates_concurrent_users():
    """Due utenti con sessioni Azure diverse, in thread diversi (come accade
    davvero: ogni turno gira nel proprio thread), non devono mai vedere il
    token dell'altro — replica lo scenario multi-tenant reale."""
    op._AZURE_SESSIONS["userA"] = _FakeCredential("userA")
    op._AZURE_SESSIONS["userB"] = _FakeCredential("userB")

    results: dict[str, str] = {}

    def worker(uid: str, key: str) -> None:
        op.set_current_user(uid)
        results[key] = azure_auth.get_arm_token()
        op.clear_current_user()

    t1 = threading.Thread(target=worker, args=("userA", "a"))
    t2 = threading.Thread(target=worker, args=("userB", "b"))
    t1.start()
    t1.join()
    t2.start()
    t2.join()

    assert results == {"a": "TOKEN-FOR-userA", "b": "TOKEN-FOR-userB"}


def test_get_arm_token_falls_back_to_default_credential_when_no_user_session(monkeypatch):
    """Nessuna sessione OAuth per l'utente corrente (o CLI locale senza
    portale, `user_id=None`) → deve ripiegare su DefaultAzureCredential,
    comportamento invariato per quel caso d'uso."""
    op.clear_current_user()  # nessun utente legato

    class _FakeDefaultCred:
        def __init__(self, *a, **k):
            pass

        def get_token(self, *scopes, **kwargs):
            class _T:
                pass

            t = _T()
            t.token = "TOKEN-DEFAULT"
            return t

    import azure.identity

    monkeypatch.setattr(azure.identity, "DefaultAzureCredential", _FakeDefaultCred)
    assert azure_auth.get_arm_token() == "TOKEN-DEFAULT"
