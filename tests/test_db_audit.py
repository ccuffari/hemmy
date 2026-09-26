"""Test di `audit.db_audit`: selezione del backend (Supabase vs file locale) e
redaction dei segreti prima dell'INSERT su Supabase (mock, nessuna rete)."""

from __future__ import annotations

from hemmy.audit.db_audit import SupabaseAuditLog, build_audit_log


class _FakeInsert:
    def __init__(self, table, payload):
        self.table = table
        self.payload = payload

    def execute(self):
        self.table.rows.append(self.payload)


class FakeTable:
    def __init__(self):
        self.rows: list[dict] = []

    def insert(self, payload):
        return _FakeInsert(self, payload)


class FakeAdminClient:
    def __init__(self):
        self.audit_log = FakeTable()

    def table(self, name):
        assert name == "audit_log"
        return self.audit_log


def test_build_audit_log_uses_local_file_when_supabase_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    from hemmy.audit.audit import AuditLog

    log = build_audit_log(str(tmp_path / "audit.jsonl"))
    assert isinstance(log, AuditLog)
    log.record("adf.list_pipelines", {"x": 1}, "success")
    assert (tmp_path / "audit.jsonl").exists()


def test_build_audit_log_uses_supabase_when_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    log = build_audit_log("/unused/path.jsonl", user_id="u1")
    assert isinstance(log, SupabaseAuditLog)


def test_supabase_audit_log_records_row_with_redaction():
    fake = FakeAdminClient()
    log = SupabaseAuditLog(user_id="u1", admin_client=fake)
    log.record(
        "sql.connect",
        {"connection_string": "Server=x;Password=SuperSegreta123;Uid=admin;"},
        "success",
    )
    assert len(fake.audit_log.rows) == 1
    row = fake.audit_log.rows[0]
    assert row["user_id"] == "u1"
    assert row["action"] == "sql.connect"
    assert row["outcome"] == "success"
    assert "SuperSegreta123" not in str(row["args"])


def test_supabase_audit_log_records_error():
    fake = FakeAdminClient()
    log = SupabaseAuditLog(user_id="u2", admin_client=fake)
    log.record("adf.start_pipeline_run", {"name": "p1"}, "error", error=RuntimeError("boom"))
    row = fake.audit_log.rows[0]
    assert row["outcome"] == "error"
    assert "boom" in row["error"]


def test_supabase_audit_log_failure_is_swallowed():
    class _BrokenTable:
        def insert(self, _payload):
            raise RuntimeError("network down")

    class _BrokenClient:
        def table(self, _name):
            return _BrokenTable()

    log = SupabaseAuditLog(user_id="u1", admin_client=_BrokenClient())
    log.record("adf.list_pipelines", {}, "success")  # non deve sollevare
