"""Test della persistenza dei plugin su Supabase (mock, nessuna rete): verificano
che `save_plugin_to_db`/`delete_plugin_from_db`/`sync_*_plugins_from_db` chiamino
le API giuste e che, quando Supabase NON è configurata, restino no-op (comportamento
locale invariato, come oggi su SQLite/filesystem puro).
"""

from __future__ import annotations

from hemmy import plugins as plugmod


class _FakeQuery:
    def __init__(self, store, op, payload=None):
        self.store = store
        self.op = op
        self.payload = dict(payload) if payload else {}
        self.filters: dict = {}

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def is_(self, col, _val):
        self.filters[col] = None
        return self

    def select(self, _cols):
        return self

    def _matched(self):
        return [
            r for r in self.store.rows
            if all(r.get(k) == v for k, v in self.filters.items())
        ]

    def execute(self):
        if self.op == "select":
            class _R:
                pass
            r = _R()
            r.data = self._matched()
            return r
        if self.op == "insert":
            row = dict(self.payload)
            row["id"] = len(self.store.rows) + 1
            self.store.rows.append(row)
            return self
        if self.op == "update":
            for r in self._matched():
                r.update(self.payload)
            return self
        if self.op == "delete":
            before = len(self.store.rows)
            self.store.rows = [r for r in self.store.rows if r not in self._matched()]
            return self
        raise AssertionError(self.op)


class FakeTable:
    def __init__(self):
        self.rows: list[dict] = []

    def select(self, _cols):
        return _FakeQuery(self, "select")

    def insert(self, payload):
        return _FakeQuery(self, "insert", payload)

    def update(self, payload):
        return _FakeQuery(self, "update", payload)

    def delete(self):
        return _FakeQuery(self, "delete")


class FakeAdminClient:
    def __init__(self):
        self.plugins = FakeTable()

    def table(self, name):
        assert name == "plugins"
        return self.plugins


def test_noop_when_supabase_not_configured(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    # Non deve sollevare nulla né chiamare get_admin_client (nessun client costruito).
    plugmod.save_plugin_to_db(module_name="x.py", source="print(1)", manifest={}, owner_user_id="u1")
    plugmod.delete_plugin_from_db(module_name="x.py", owner_user_id="u1")
    plugmod.sync_user_plugins_from_db("u1")
    plugmod.sync_global_plugins_from_db()


def test_save_plugin_inserts_then_updates(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    fake = FakeAdminClient()
    monkeypatch.setattr("hemmy.db.supabase_client.get_admin_client", lambda: fake)

    plugmod.save_plugin_to_db(
        module_name="demo__ping.py",
        source="def run(**k): return {}\n",
        manifest={"tools": [{"name": "demo.ping"}]},
        owner_user_id="u1",
        created_by="u1",
    )
    assert len(fake.plugins.rows) == 1
    assert fake.plugins.rows[0]["scope"] == "user"
    assert fake.plugins.rows[0]["owner_user_id"] == "u1"

    # Una seconda scrittura con lo stesso nome/owner aggiorna la riga esistente, non ne crea un'altra.
    plugmod.save_plugin_to_db(
        module_name="demo__ping.py",
        source="def run(**k): return {'v':2}\n",
        manifest={"tools": [{"name": "demo.ping"}]},
        owner_user_id="u1",
    )
    assert len(fake.plugins.rows) == 1
    assert "v" in fake.plugins.rows[0]["source_code"]


def test_save_plugin_global_scope_when_no_owner(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    fake = FakeAdminClient()
    monkeypatch.setattr("hemmy.db.supabase_client.get_admin_client", lambda: fake)

    plugmod.save_plugin_to_db(module_name="shared.py", source="x=1\n", manifest={})
    assert fake.plugins.rows[0]["scope"] == "global"
    assert fake.plugins.rows[0]["owner_user_id"] is None


def test_delete_plugin_from_db(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    fake = FakeAdminClient()
    monkeypatch.setattr("hemmy.db.supabase_client.get_admin_client", lambda: fake)

    plugmod.save_plugin_to_db(module_name="demo.py", source="x=1\n", manifest={}, owner_user_id="u1")
    assert len(fake.plugins.rows) == 1
    plugmod.delete_plugin_from_db(module_name="demo.py", owner_user_id="u1")
    assert len(fake.plugins.rows) == 0


def test_sync_user_plugins_rematerializes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    fake = FakeAdminClient()
    fake.plugins.rows.append(
        {
            "scope": "user",
            "owner_user_id": "u1",
            "module_name": "demo__ping.py",
            "source_code": "def run(**k): return {'ok': True}\n",
            "status": "active",
        }
    )
    monkeypatch.setattr("hemmy.db.supabase_client.get_admin_client", lambda: fake)
    monkeypatch.setattr(plugmod, "user_plugins_dir", lambda uid: tmp_path / "u1")

    plugmod.sync_user_plugins_from_db("u1")
    written = tmp_path / "u1" / "demo__ping.py"
    assert written.exists()
    assert "ok" in written.read_text()


def test_sync_global_plugins_rematerializes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    fake = FakeAdminClient()
    fake.plugins.rows.append(
        {
            "scope": "global",
            "owner_user_id": None,
            "module_name": "shared.py",
            "source_code": "x = 1\n",
            "status": "active",
        }
    )
    monkeypatch.setattr("hemmy.db.supabase_client.get_admin_client", lambda: fake)
    monkeypatch.setattr(plugmod, "_RUNTIME_PLUGINS_DIR", tmp_path)

    plugmod.sync_global_plugins_from_db()
    assert (tmp_path / "shared.py").exists()
