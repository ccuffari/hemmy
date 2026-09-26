"""Test del modulo `auth.oauth_providers` (federazione per-utente Azure/GitHub).

Copre: stato in-memory (nessuna persistenza su disco), transizioni di stato,
disconnessione, e il "ponte" con `clients._credential()` /
`cicd_tools.resolve_github_token()` tramite `set_current_user`/`clear_current_user`.
Nessuna chiamata di rete reale: MSAL e le richieste HTTP a GitHub sono mockate.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from hemmy.auth import oauth_providers as op


def _clear_all_state() -> None:
    op._AZURE_SESSIONS.clear()
    op._AZURE_PENDING.clear()
    op._AZURE_DEVOPS_ORGS.clear()
    op._GITHUB_SESSIONS.clear()
    op._GITHUB_PENDING.clear()
    op._GCP_SESSIONS.clear()
    op._GCP_PENDING.clear()
    op._AWS_SSO_SESSIONS.clear()
    op._AWS_ROLE_CREDS.clear()
    op._AWS_PENDING.clear()
    op.clear_current_user()


@pytest.fixture(autouse=True)
def _clean_state():
    """Ogni test parte da stato vuoto e lo ripulisce alla fine (stato è globale)."""
    _clear_all_state()
    yield
    _clear_all_state()


# --------------------------------------------------------------------------- Azure


def test_azure_status_disconnected_by_default():
    st = op.azure_status("u1")
    assert st == {"connected": False, "status": "disconnected", "message": None}


def test_azure_status_connected_after_session_set():
    fake_cred = object()
    op._AZURE_SESSIONS["u1"] = fake_cred
    op._AZURE_PENDING["u1"] = {"status": "connected", "message": "mario@contoso.com"}
    st = op.azure_status("u1")
    assert st["connected"] is True
    assert st["status"] == "connected"
    assert st["message"] == "mario@contoso.com"


def test_azure_status_isolated_per_user():
    op._AZURE_SESSIONS["u1"] = object()
    op._AZURE_PENDING["u1"] = {"status": "connected", "message": "u1@contoso.com"}
    assert op.azure_status("u1")["connected"] is True
    assert op.azure_status("u2")["connected"] is False


def test_disconnect_azure_clears_session_and_pending():
    op._AZURE_SESSIONS["u1"] = object()
    op._AZURE_PENDING["u1"] = {"status": "connected", "message": "x"}
    op.disconnect_azure("u1")
    assert op.azure_status("u1") == {"connected": False, "status": "disconnected", "message": None}


def test_get_current_azure_credential_without_bound_user_is_none():
    op._AZURE_SESSIONS["u1"] = object()
    assert op.get_current_azure_credential() is None


def test_get_current_azure_credential_follows_bound_user():
    fake_cred = object()
    op._AZURE_SESSIONS["u1"] = fake_cred
    op.set_current_user("u1")
    assert op.get_current_azure_credential() is fake_cred
    op.set_current_user("u2")
    assert op.get_current_azure_credential() is None


def test_clear_current_user_breaks_the_bridge():
    op._AZURE_SESSIONS["u1"] = object()
    op.set_current_user("u1")
    op.clear_current_user()
    assert op.get_current_azure_credential() is None


def test_start_azure_connect_returns_code_and_sets_waiting(monkeypatch):
    """`initiate_device_flow` gira nel thread della richiesta: lo user_code deve
    essere disponibile SUBITO nella risposta, senza dover fare polling."""

    class _FakeApp:
        def initiate_device_flow(self, scopes):
            return {
                "user_code": "AZ-CODE",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900,
                "interval": 5,
            }

        def acquire_token_by_device_flow(self, flow):
            return {"access_token": "tok", "expires_in": 3600}

        def get_accounts(self):
            return [{"username": "mario@contoso.com"}]

    monkeypatch.setattr(op, "_msal_app", lambda tenant: _FakeApp())

    out = op.start_azure_connect("u1")
    assert out["user_code"] == "AZ-CODE"
    assert out["verification_uri"] == "https://microsoft.com/devicelogin"

    # Il thread di polling (fittizio, immediato) può già aver finito: l'unica
    # garanzia forte è sul valore di ritorno sincrono già verificato sopra.
    assert op.azure_status("u1")["status"] in ("waiting", "connected")

    deadline = time.time() + 5
    while time.time() < deadline and op.azure_status("u1")["status"] != "connected":
        time.sleep(0.05)

    st = op.azure_status("u1")
    assert st["status"] == "connected"
    assert st["message"] == "mario@contoso.com"
    assert op.get_azure_credential("u1") is not None


def test_start_azure_connect_worker_failure_sets_error(monkeypatch):
    class _FakeApp:
        def initiate_device_flow(self, scopes):
            return {"user_code": "AZ-CODE", "verification_uri": "https://microsoft.com/devicelogin"}

        def acquire_token_by_device_flow(self, flow):
            raise RuntimeError("login annullato")

        def get_accounts(self):
            return []

    monkeypatch.setattr(op, "_msal_app", lambda tenant: _FakeApp())

    op.start_azure_connect("u1")
    deadline = time.time() + 5
    while time.time() < deadline and op.azure_status("u1")["status"] not in ("error",):
        time.sleep(0.05)

    st = op.azure_status("u1")
    assert st["status"] == "error"
    assert "login annullato" in st["message"]


def test_start_azure_connect_bad_flow_raises_immediately(monkeypatch):
    """Se `initiate_device_flow` fallisce, l'errore va sollevato SUBITO (nel
    thread della richiesta), non silenziosamente in background."""

    class _FakeApp:
        def initiate_device_flow(self, scopes):
            return {"error_description": "app non trovata"}

    monkeypatch.setattr(op, "_msal_app", lambda tenant: _FakeApp())
    with pytest.raises(RuntimeError, match="app non trovata"):
        op.start_azure_connect("u1")


def test_azure_credential_get_token_raises_when_silent_fails():
    """Non deve MAI riaprire un popup interattivo a metà di una chiamata tool."""

    class _FakeApp:
        def acquire_token_silent(self, scopes, account):
            return None

    cred = op.AzureUserCredential(_FakeApp(), account=None)
    with pytest.raises(RuntimeError, match="Ricollega Azure"):
        cred.get_token("https://management.azure.com/.default")


def test_azure_credential_get_token_returns_access_token_on_success():
    class _FakeApp:
        def acquire_token_silent(self, scopes, account):
            return {"access_token": "abc123", "expires_in": 3600}

    cred = op.AzureUserCredential(_FakeApp(), account=None)
    token = cred.get_token("https://management.azure.com/.default")
    assert token.token == "abc123"


# -------------------------------------------------------------------------- GitHub


def test_github_status_disconnected_by_default():
    st = op.github_status("u1")
    assert st["connected"] is False
    assert st["status"] == "disconnected"


def test_github_status_connected():
    op._GITHUB_SESSIONS["u1"] = {"token": "ghp_x", "login": "marior"}
    op._GITHUB_PENDING["u1"] = {"status": "connected", "login": "marior"}
    st = op.github_status("u1")
    assert st["connected"] is True
    assert st["login"] == "marior"


def test_github_status_waiting_exposes_user_code():
    op._GITHUB_PENDING["u1"] = {
        "status": "waiting",
        "user_code": "ABCD-1234",
        "verification_uri": "https://github.com/login/device",
    }
    st = op.github_status("u1")
    assert st["status"] == "waiting"
    assert st["user_code"] == "ABCD-1234"
    assert st["verification_uri"] == "https://github.com/login/device"


class _FakeUsersStore:
    """Fake minimale di `UserStore`/`SupabaseUserStore`: solo i tre metodi
    usati dal BYOK GitHub (`get_secret`/`set_secret`/`delete_secret`),
    in memoria, per-user_id."""

    def __init__(self) -> None:
        self._secrets: dict[Any, dict[str, str]] = {}

    def get_secret(self, user_id: Any, name: str) -> str | None:
        return self._secrets.get(user_id, {}).get(name)

    def set_secret(self, user_id: Any, name: str, value: str) -> None:
        self._secrets.setdefault(user_id, {})[name] = value

    def delete_secret(self, user_id: Any, name: str) -> None:
        self._secrets.get(user_id, {}).pop(name, None)


def test_start_github_connect_requires_client_id(monkeypatch):
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    users = _FakeUsersStore()
    with pytest.raises(RuntimeError, match="GitHub OAuth non configurato"):
        op.start_github_connect(users, "u1")


def test_is_github_oauth_configured(monkeypatch):
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    users = _FakeUsersStore()
    assert op.is_github_oauth_configured(users, "u1") is False
    # BYOK: l'utente configura il proprio Client ID.
    op.save_github_oauth_client_id(users, "u1", "cid-byok")
    assert op.is_github_oauth_configured(users, "u1") is True
    # Un altro utente, senza il proprio, non lo eredita da "u1".
    assert op.is_github_oauth_configured(users, "u2") is False
    # Fallback di piattaforma via env var per chi non ha configurato il suo.
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "cid-platform")
    assert op.is_github_oauth_configured(users, "u2") is True
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "   ")
    assert op.is_github_oauth_configured(users, "u2") is False


def test_start_github_connect_returns_device_code(monkeypatch):
    users = _FakeUsersStore()
    op.save_github_oauth_client_id(users, "u1", "cid123")
    monkeypatch.setattr(
        op,
        "_http_json",
        lambda url, body: {
            "device_code": "dev123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        },
    )
    # Evitiamo che parta davvero il thread di polling verso GitHub.
    monkeypatch.setattr(op.threading, "Thread", lambda target, args, daemon: _NoopThread())

    out = op.start_github_connect(users, "u1")
    assert out["user_code"] == "ABCD-1234"
    assert out["verification_uri"] == "https://github.com/login/device"
    assert op.github_status("u1")["status"] == "waiting"


class _NoopThread:
    def start(self):
        pass


def test_disconnect_github_clears_session_and_pending():
    op._GITHUB_SESSIONS["u1"] = {"token": "t", "login": "x"}
    op._GITHUB_PENDING["u1"] = {"status": "connected", "login": "x"}
    op.disconnect_github("u1")
    st = op.github_status("u1")
    assert st["connected"] is False
    assert st["status"] == "disconnected"


def test_get_current_github_token_follows_bound_user():
    op._GITHUB_SESSIONS["u1"] = {"token": "ghp_x", "login": "marior"}
    assert op.get_current_github_token() is None  # nessun utente legato ancora
    op.set_current_user("u1")
    assert op.get_current_github_token() == "ghp_x"
    op.set_current_user("u2")
    assert op.get_current_github_token() is None


# --------------------------------------------------------------- ponte con i client


def test_clients_credential_uses_bound_user_oauth_session(monkeypatch):
    """`infra.clients._credential()` deve usare la sessione OAuth dell'utente legato,
    non il fallback su Service Principal / Azure CLI / browser interattivo."""
    from hemmy.infra import clients

    fake_cred = object()
    op._AZURE_SESSIONS["u1"] = fake_cred
    op.set_current_user("u1")
    assert clients._credential() is fake_cred


def test_cicd_resolve_github_token_uses_bound_user_oauth_session(monkeypatch):
    from hemmy.tools.cicd import cicd_tools

    op._GITHUB_SESSIONS["u1"] = {"token": "ghp_from_oauth", "login": "marior"}
    op.set_current_user("u1")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cicd_tools.resolve_github_token() == "ghp_from_oauth"


# --------------------------------------------------------------------- Azure DevOps


class _FakeAzureApp:
    """MSAL app fittizia: l'acquisizione silenziosa per lo scope Azure DevOps
    riesce solo se lo scope richiesto è quello atteso."""

    def acquire_token_silent(self, scopes, account):
        if scopes == [op._AZURE_DEVOPS_SCOPE]:
            return {"access_token": "azdo-tok", "expires_in": 3600}
        return None


def test_connect_azure_devops_requires_azure_session_first():
    with pytest.raises(RuntimeError, match="Connetti prima Azure"):
        op.connect_azure_devops("u1", "myorg")


def test_connect_azure_devops_reuses_azure_session():
    op._AZURE_SESSIONS["u1"] = op.AzureUserCredential(_FakeAzureApp(), account=None)
    info = op.connect_azure_devops("u1", "https://dev.azure.com/myorg/")
    assert info["organization"] == "myorg"  # normalizzato: niente URL/slash
    st = op.azure_devops_status("u1")
    assert st["connected"] is True
    assert st["organization"] == "myorg"


def test_connect_azure_devops_fails_if_tenant_denies_scope():
    class _DenyApp:
        def acquire_token_silent(self, scopes, account):
            return None

    op._AZURE_SESSIONS["u1"] = op.AzureUserCredential(_DenyApp(), account=None)
    with pytest.raises(RuntimeError, match="tenant non consente"):
        op.connect_azure_devops("u1", "myorg")
    # Non deve restare "mezzo connesso" se la validazione fallisce.
    assert op.azure_devops_status("u1")["connected"] is False


def test_azure_devops_status_disconnects_if_azure_session_dropped():
    op._AZURE_SESSIONS["u1"] = op.AzureUserCredential(_FakeAzureApp(), account=None)
    op.connect_azure_devops("u1", "myorg")
    op.disconnect_azure("u1")
    st = op.azure_devops_status("u1")
    assert st["connected"] is False


def test_get_current_azure_devops_credential_follows_bound_user():
    op._AZURE_SESSIONS["u1"] = op.AzureUserCredential(_FakeAzureApp(), account=None)
    op.connect_azure_devops("u1", "myorg")
    op.set_current_user("u1")
    cred = op.get_current_azure_devops_credential()
    assert cred == {"organization": "myorg", "token": "azdo-tok"}
    op.set_current_user("u2")
    assert op.get_current_azure_devops_credential() is None


# ----------------------------------------------------------------------------- GCP


def test_is_gcp_oauth_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    assert op.is_gcp_oauth_configured() is False
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    assert op.is_gcp_oauth_configured() is False  # manca il secret
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    assert op.is_gcp_oauth_configured() is True


def test_start_gcp_connect_requires_client_config(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_ID"):
        op.start_gcp_connect("u1")


def test_start_gcp_connect_returns_device_code_and_polls(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")

    def _fake_http_json(url, body):
        if url == op._GOOGLE_DEVICE_CODE_URL:
            return {
                "device_code": "dev123",
                "user_code": "GCP-CODE",
                "verification_url": "https://www.google.com/device",
                "expires_in": 1800,
                "interval": 5,
            }
        raise AssertionError(f"URL inatteso: {url}")

    monkeypatch.setattr(op, "_http_json", _fake_http_json)
    monkeypatch.setattr(op.threading, "Thread", lambda target, args, daemon: _NoopThread())

    out = op.start_gcp_connect("u1")
    assert out["user_code"] == "GCP-CODE"
    assert out["verification_uri"] == "https://www.google.com/device"
    assert op.gcp_status("u1")["status"] == "waiting"


def test_poll_gcp_device_success_stores_session(monkeypatch):
    monkeypatch.setattr(op, "_fetch_gcp_email", lambda tok: "mario@gmail.com")

    def _fake_http_json(url, body):
        return {"access_token": "gcp-tok", "refresh_token": "rt", "expires_in": 3600}

    monkeypatch.setattr(op, "_http_json", _fake_http_json)
    op._poll_gcp_device("u1", "cid", "sec", {"device_code": "d", "interval": 0, "expires_in": 5})

    st = op.gcp_status("u1")
    assert st["status"] == "connected"
    assert st["message"] == "mario@gmail.com"


def test_get_gcp_access_token_refreshes_when_expired(monkeypatch):
    op._GCP_SESSIONS["u1"] = {
        "access_token": "old",
        "refresh_token": "rt",
        "expires_at": time.time() - 10,  # già scaduto
        "client_id": "cid",
        "client_secret": "sec",
        "email": "mario@gmail.com",
    }
    monkeypatch.setattr(
        op, "_http_json", lambda url, body: {"access_token": "fresh", "expires_in": 3600}
    )
    assert op.get_gcp_access_token("u1") == "fresh"


def test_get_gcp_access_token_reuses_valid_token(monkeypatch):
    op._GCP_SESSIONS["u1"] = {
        "access_token": "still-valid",
        "refresh_token": "rt",
        "expires_at": time.time() + 3600,
        "client_id": "cid",
        "client_secret": "sec",
        "email": "mario@gmail.com",
    }

    def _boom(*a, **k):
        raise AssertionError("non doveva chiamare il refresh")

    monkeypatch.setattr(op, "_http_json", _boom)
    assert op.get_gcp_access_token("u1") == "still-valid"


def test_get_current_gcp_access_token_follows_bound_user():
    op._GCP_SESSIONS["u1"] = {
        "access_token": "tok",
        "refresh_token": None,
        "expires_at": time.time() + 3600,
        "client_id": "cid",
        "client_secret": "sec",
        "email": "x",
    }
    op.set_current_user("u1")
    assert op.get_current_gcp_access_token() == "tok"
    op.set_current_user("u2")
    assert op.get_current_gcp_access_token() is None


# ----------------------------------------------------------------------------- AWS


def test_start_aws_sso_connect_requires_start_url():
    with pytest.raises(RuntimeError, match="start URL"):
        op.start_aws_sso_connect("u1", "", "eu-west-1")


class _FakeOidcClient:
    def __init__(self, token_result=None, raise_pending_times=0):
        self._token_result = token_result or {"accessToken": "sso-tok"}
        self._raise_pending_times = raise_pending_times
        self._calls = 0

        class _Exceptions:
            class AuthorizationPendingException(Exception):
                pass

            class SlowDownException(Exception):
                pass

        self.exceptions = _Exceptions()

    def register_client(self, clientName, clientType):
        return {"clientId": "cid", "clientSecret": "csecret"}

    def start_device_authorization(self, clientId, clientSecret, startUrl):
        return {
            "deviceCode": "dcode",
            "userCode": "AWS-CODE",
            "verificationUri": "https://device.sso.amazonaws.com",
            "verificationUriComplete": "https://device.sso.amazonaws.com/?code=AWS-CODE",
            "expiresIn": 600,
            "interval": 0,
        }

    def create_token(self, clientId, clientSecret, grantType, deviceCode):
        self._calls += 1
        if self._calls <= self._raise_pending_times:
            raise self.exceptions.AuthorizationPendingException()
        return self._token_result


class _FakeSsoClient:
    def __init__(self, accounts_roles: dict[str, list[str]]):
        self._accounts_roles = accounts_roles

    def list_accounts(self, accessToken, nextToken=None):
        return {
            "accountList": [
                {"accountId": aid, "accountName": aid} for aid in self._accounts_roles
            ]
        }

    def list_account_roles(self, accessToken, accountId):
        return {"roleList": [{"roleName": r} for r in self._accounts_roles[accountId]]}

    def get_role_credentials(self, accessToken, accountId, roleName):
        return {
            "roleCredentials": {
                "accessKeyId": f"AK-{accountId}-{roleName}",
                "secretAccessKey": "secret",
                "sessionToken": "session",
                "expiration": int((time.time() + 3600) * 1000),
            }
        }


def test_start_aws_sso_connect_single_role_autofinalizes(monkeypatch):
    fake_oidc = _FakeOidcClient()
    fake_sso = _FakeSsoClient({"111111111111": ["AdministratorAccess"]})

    def _fake_boto3_client(service, region_name=None):
        return fake_oidc if service == "sso-oidc" else fake_sso

    import sys
    monkeypatch.setitem(sys.modules, "boto3", type("_M", (), {"client": staticmethod(_fake_boto3_client)}))

    out = op.start_aws_sso_connect("u1", "https://acme.awsapps.com/start", "eu-west-1")
    assert out["user_code"] == "AWS-CODE"
    # Il thread di polling (fittizio, interval=0) può già aver finito: l'unica
    # garanzia forte è sul valore di ritorno sincrono già verificato sopra.
    assert op.aws_sso_status("u1")["status"] in ("waiting", "connected")

    deadline = time.time() + 5
    while time.time() < deadline and op.aws_sso_status("u1")["status"] == "waiting":
        time.sleep(0.02)

    st = op.aws_sso_status("u1")
    assert st["status"] == "connected"
    creds = op.get_aws_sso_credentials("u1")
    assert creds["access_key_id"] == "AK-111111111111-AdministratorAccess"


def test_start_aws_sso_connect_multiple_roles_requires_selection(monkeypatch):
    fake_oidc = _FakeOidcClient()
    fake_sso = _FakeSsoClient({
        "111111111111": ["AdministratorAccess"],
        "222222222222": ["ReadOnlyAccess"],
    })

    def _fake_boto3_client(service, region_name=None):
        return fake_oidc if service == "sso-oidc" else fake_sso

    import sys
    monkeypatch.setitem(sys.modules, "boto3", type("_M", (), {"client": staticmethod(_fake_boto3_client)}))

    op.start_aws_sso_connect("u1", "https://acme.awsapps.com/start", "eu-west-1")

    deadline = time.time() + 5
    while time.time() < deadline and op.aws_sso_status("u1")["status"] == "waiting":
        time.sleep(0.02)

    st = op.aws_sso_status("u1")
    assert st["status"] == "select_role"
    assert len(st["options"]) == 2

    op.select_aws_role("u1", "222222222222", "ReadOnlyAccess")
    st = op.aws_sso_status("u1")
    assert st["status"] == "connected"
    creds = op.get_aws_sso_credentials("u1")
    assert creds["account_id"] == "222222222222"


def test_disconnect_aws_sso_clears_everything():
    op._AWS_SSO_SESSIONS["u1"] = {"access_token": "t", "region": "eu-west-1"}
    op._AWS_ROLE_CREDS["u1"] = {"account_id": "1", "role_name": "r"}
    op._AWS_PENDING["u1"] = {"status": "connected"}
    op.disconnect_aws_sso("u1")
    assert op.aws_sso_status("u1")["status"] == "disconnected"
    assert op.get_aws_sso_credentials("u1") is None


def test_get_current_aws_sso_credentials_follows_bound_user():
    op._AWS_ROLE_CREDS["u1"] = {
        "account_id": "111", "role_name": "Admin",
        "access_key_id": "AK", "secret_access_key": "SK", "session_token": "ST",
        "expiration": int((time.time() + 3600) * 1000), "region": "eu-west-1",
    }
    op.set_current_user("u1")
    assert op.get_current_aws_sso_credentials()["access_key_id"] == "AK"
    op.set_current_user("u2")
    assert op.get_current_aws_sso_credentials() is None
