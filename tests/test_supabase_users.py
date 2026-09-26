"""Test di `auth.supabase_users.SupabaseUserStore` con client Supabase FINTI
(nessuna rete): verificano che l'adapter mappi correttamente l'interfaccia comune
(register/authenticate/create_session/resolve_session/settings/secrets/
conversazioni) sulle chiamate REST/Auth che farebbe il client reale.
"""

from __future__ import annotations

import base64
import time

import jwt as pyjwt
import pytest

from hemmy.auth.supabase_users import SupabaseUserStore

_JWT_SECRET = "test-secret-not-real-but-long-enough-for-hs256-32bytes"


# --------------------------------------------------------------- fake Supabase


class _FakeResp:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    def __init__(self, table, op, payload=None):
        self.table = table
        self.op = op
        self.payload = payload
        self.filters: dict = {}
        self._count_mode = None
        self._limit = None

    def select(self, cols, count=None):
        self._count_mode = count
        self._cols = [c.strip() for c in cols.split(",")] if cols and cols != "*" else None
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matched(self):
        rows = self.table.rows
        matched = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        return matched[: self._limit] if self._limit else matched

    def execute(self):
        if self.op == "select":
            matched = self._matched()
            if self._count_mode == "exact":
                return _FakeResp(data=None, count=len(matched))
            if self._cols:
                matched = [{c: r.get(c) for c in self._cols} for r in matched]
            return _FakeResp(data=matched)
        if self.op == "update":
            matched = self._matched()
            for r in matched:
                r.update(self.payload)
            return _FakeResp(data=matched)
        if self.op == "upsert":
            existing = None
            for r in self.table.rows:
                if all(r.get(k) == self.payload.get(k) for k in self.table.pk):
                    existing = r
                    break
            if existing is not None:
                existing.update(self.payload)
                return _FakeResp(data=[existing])
            row = dict(self.payload)
            self.table.rows.append(row)
            return _FakeResp(data=[row])
        raise AssertionError(f"op non gestita: {self.op}")


class _FakeTable:
    def __init__(self, name, pk):
        self.name = name
        self.pk = pk
        self.rows: list[dict] = []

    def select(self, cols, count=None):
        return _FakeQuery(self, "select").select(cols, count)

    def update(self, payload):
        return _FakeQuery(self, "update", payload)

    def upsert(self, payload):
        return _FakeQuery(self, "upsert", payload)


class _FakeAdminAuthAdmin:
    def __init__(self, profiles_table, creds_store):
        self._profiles = profiles_table
        self._creds = creds_store
        self._n = 0

    def create_user(self, payload):
        self._n += 1
        uid = f"user-{self._n}"
        username = payload["user_metadata"]["username"]
        self._profiles.rows.append(
            {"id": uid, "username": username, "role": "engineer", "settings": {}}
        )
        self._creds[payload["email"]] = {"id": uid, "password": payload["password"]}

        class _R:
            pass

        r = _R()
        r.user = type("U", (), {"id": uid})()
        return r


class _FakeAdminAuth:
    def __init__(self, profiles_table, creds_store):
        self.admin = _FakeAdminAuthAdmin(profiles_table, creds_store)


class FakeAdminClient:
    def __init__(self, creds_store):
        self.tables = {
            "profiles": _FakeTable("profiles", ["id"]),
            "user_secrets": _FakeTable("user_secrets", ["user_id", "name"]),
            "conversations": _FakeTable("conversations", ["user_id"]),
        }
        self.auth = _FakeAdminAuth(self.tables["profiles"], creds_store)

    def table(self, name):
        return self.tables[name]


class _FakeAnonAuth:
    def __init__(self, creds_store):
        self._creds = creds_store
        self.signed_out: list[str] = []

    def sign_in_with_password(self, payload):
        creds = self._creds.get(payload["email"])
        if not creds or creds["password"] != payload["password"]:
            raise RuntimeError("Invalid login credentials")
        uid = creds["id"]
        access = pyjwt.encode(
            {"sub": uid, "aud": "authenticated", "exp": int(time.time()) + 3600},
            _JWT_SECRET,
            algorithm="HS256",
        )

        class _S:
            pass

        class _U:
            pass

        session = _S()
        session.access_token = access
        session.refresh_token = f"refresh-{uid}"
        user = _U()
        user.id = uid

        class _R:
            pass

        r = _R()
        r.session = session
        r.user = user
        return r

    def sign_out(self, _access_token):
        self.signed_out.append(_access_token)


class FakeAnonClient:
    def __init__(self, creds_store):
        self.auth = _FakeAnonAuth(creds_store)


@pytest.fixture()
def store(tmp_path):
    creds: dict = {}
    admin = FakeAdminClient(creds)
    anon = FakeAnonClient(creds)
    master_key_path = tmp_path / ".master.key"
    return SupabaseUserStore(
        admin_client=admin,
        anon_client=anon,
        jwt_secret_value=_JWT_SECRET,
        master_key_path=str(master_key_path),
    )


# ------------------------------------------------------------------------ tests


def test_register_creates_user_and_profile(store):
    u = store.register("alice", "supersegreta1", role="architect", email="alice@example.com")
    assert u["username"] == "alice"
    assert u["role"] == "architect"
    fetched = store.get_user(u["id"])
    assert fetched == {"id": u["id"], "username": "alice", "role": "architect"}


def test_register_requires_email(store):
    with pytest.raises(ValueError, match="email"):
        store.register("bob", "longenough123")


def test_register_requires_valid_role(store):
    with pytest.raises(ValueError, match="ruolo"):
        store.register("bob", "longenough123", role="admin", email="bob@example.com")


def test_authenticate_wrong_password_returns_none(store):
    store.register("carla", "longenough123", email="carla@example.com")
    assert store.authenticate("carla@example.com", "wrongpwd1") is None


def test_authenticate_unknown_email_returns_none(store):
    assert store.authenticate("ghost@example.com", "whatever1") is None


def test_full_login_session_roundtrip(store):
    u = store.register("dave", "longenough123", email="dave@example.com")
    profile = store.authenticate("dave@example.com", "longenough123")
    assert profile["id"] == u["id"]

    token = store.create_session(u["id"])
    assert "::" in token

    resolved = store.resolve_session(token)
    assert resolved == {"id": u["id"], "username": "dave", "role": "engineer"}


def test_create_session_without_prior_authenticate_raises(store):
    with pytest.raises(ValueError, match="nessuna sessione"):
        store.create_session("some-id")


def test_resolve_session_rejects_garbage_token(store):
    assert store.resolve_session("") is None
    assert store.resolve_session("not-a-valid-token") is None
    assert store.resolve_session("garbage::garbage") is None


def test_resolve_session_rejects_tampered_signature(store):
    bad = pyjwt.encode(
        {"sub": "x", "aud": "authenticated", "exp": int(time.time()) + 3600},
        "a-completely-different-wrong-secret-value-32b",
        algorithm="HS256",
    )
    assert store.resolve_session(f"{bad}::refresh") is None


def test_revoke_session_calls_sign_out(store):
    u = store.register("emma", "longenough123", email="emma@example.com")
    store.authenticate("emma@example.com", "longenough123")
    token = store.create_session(u["id"])
    store.revoke_session(token)
    assert len(store._anon().auth.signed_out) == 1


def test_settings_roundtrip_and_forbidden_keys(store):
    u = store.register("frank", "longenough123", email="frank@example.com")
    merged = store.set_settings(u["id"], {"tenant": "contoso"})
    assert merged["tenant"] == "contoso"
    assert store.get_settings(u["id"])["tenant"] == "contoso"
    with pytest.raises(ValueError, match="segrete"):
        store.set_settings(u["id"], {"api_key": "x"})


def test_secrets_are_encrypted_and_roundtrip(store):
    u = store.register("gina", "longenough123", email="gina@example.com")
    store.set_secret(u["id"], "deepseek_api_key", "sk-super-secret")
    raw_row = store._admin().tables["user_secrets"].rows[0]
    assert raw_row["value_enc"] != "sk-super-secret"  # mai in chiaro nel "DB"
    assert store.get_secret(u["id"], "deepseek_api_key") == "sk-super-secret"
    assert store.list_secret_names(u["id"]) == ["deepseek_api_key"]
    assert store.get_secret(u["id"], "missing") is None


def test_conversation_lifecycle(store):
    u = store.register("hugo", "longenough123", email="hugo@example.com")
    assert store.get_conversation(u["id"]) == []
    assert store.get_pending(u["id"]) is None

    store.set_pending(u["id"], "domanda in corso")
    assert store.get_pending(u["id"]) == "domanda in corso"

    msgs = [{"role": "user", "content": "ciao"}]
    store.save_conversation(u["id"], msgs)
    assert store.get_conversation(u["id"]) == msgs

    store.reset_conversation(u["id"])
    assert store.get_conversation(u["id"]) == []
    assert store.get_pending(u["id"]) is None


def test_count_users(store):
    assert store.count_users() == 0
    store.register("ivy", "longenough123", email="ivy@example.com")
    store.register("jack", "longenough123", email="jack@example.com")
    assert store.count_users() == 2
