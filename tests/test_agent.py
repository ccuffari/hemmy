"""Suite di test dell'agente ADF.

Copre: guardrail (letture/scritture/approvazione/DROP/INSERT), parsing ReAct
(JSON annidato), redaction segreti, cache segreti, schema_diff, persistenza del
contesto tra turni e audit-log.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("DEEPSEEK_API_KEY", "dummy")

from hemmy.audit.audit import AuditLog
from hemmy.core.agent import ADFAgent
from hemmy.docs.docgen import DocStore
from hemmy.guardrails.guardrails import Guardrails, GuardrailViolation
from hemmy.memory.memory import ShortTermMemory
from hemmy.tools.iac import iac_tools
from hemmy.tools.keyvault import keyvault_tools
from hemmy.tools.cicd import cicd_tools
from hemmy.tools.github import github_tools
from hemmy.tools.lock import lock_tools
from hemmy.tools.network import network_tools
from hemmy.tools.rbac import rbac_tools
from hemmy.tools.remediation import remediation_tools
from hemmy.utils.helpers import SessionSecretProvider, redact_secrets, schema_diff


# --------------------------------------------------------------------- fixtures


def _policy() -> dict:
    return {
        "allowed_actions": ["sql.get_table_schema", "adf.list_pipelines"],
        "write_actions": {
            "require_human_approval": True,
            "actions": ["sql.execute_write", "sql.execute_write_for_connection"],
        },
        "validation": {"block_destructive_sql": True, "always_deny_sql": []},
    }


def _make_agent(tools=None, guardrails=None):
    config = {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "max_iterations": 5,
        "max_history": 40,
    }
    prompts = {"system": "SYS", "planner": "PLAN", "guardrail": "GR"}

    class _G:
        def check(self, a, args):
            pass

        def is_write_action(self, a):
            return False

    return ADFAgent(
        config,
        prompts,
        tools or {},
        guardrails or _G(),
        ShortTermMemory(),
        tool_docs={},
    )


# -------------------------------------------------------------------- guardrail


def test_read_action_allowed():
    Guardrails(_policy()).check("sql.get_table_schema", {"table": "dbo.x"})


def test_write_requires_approval():
    g = Guardrails(_policy())
    g._request_human_approval = lambda a, args, preview=None: False
    with pytest.raises(GuardrailViolation):
        g.check("sql.execute_write", {"query": "INSERT INTO t VALUES (1)"})


def test_insert_allowed_with_approval():
    g = Guardrails(_policy())
    g._request_human_approval = lambda a, args, preview=None: True
    g.check("sql.execute_write_for_connection", {"query": "INSERT INTO t VALUES (1)"})


def test_id_insert_column_not_false_positive():
    g = Guardrails(_policy())
    g._request_human_approval = lambda a, args, preview=None: True
    g.check("sql.execute_write_for_connection", {"query": "CREATE TABLE t (id_insert INT)"})


def test_drop_allowed_when_not_in_always_deny():
    g = Guardrails(_policy())
    g._request_human_approval = lambda a, args, preview=None: True
    g.check("sql.execute_write", {"query": "DROP TABLE t"})


def test_drop_blocked_when_in_always_deny():
    policy = _policy()
    policy["validation"]["always_deny_sql"] = ["DROP", "TRUNCATE"]
    g = Guardrails(policy)
    g._request_human_approval = lambda a, args, preview=None: True
    with pytest.raises(GuardrailViolation):
        g.check("sql.execute_write", {"query": "DROP TABLE t"})


def test_guardrail_shows_preview_at_approval():
    policy = _policy()
    policy["write_actions"]["actions"].append("iac.apply")
    captured = {}
    g = Guardrails(policy, previews={"iac.apply": lambda args: "PLAN: 1 to add, 0 to change"})

    def fake_approval(action, args, preview=None):
        captured["preview"] = preview
        return True

    g._request_human_approval = fake_approval
    g.check("iac.apply", {})
    assert captured["preview"] == "PLAN: 1 to add, 0 to change"


# ---------------------------------------------------------------------- parsing


def test_parse_nested_json():
    text = (
        "Action: adf.create_dataset\n"
        'Action Input: {"name": "DS", "definition": {"properties": '
        '{"typeProperties": {"sheetName": "Sheet1"}}}}\n'
        "Observation: x"
    )
    action, args = ADFAgent._parse_action(text)
    assert action == "adf.create_dataset"
    assert args["definition"]["properties"]["typeProperties"]["sheetName"] == "Sheet1"


def test_parse_final_answer_has_no_action():
    action, args = ADFAgent._parse_action("Final Answer: fatto")
    assert action is None


# --------------------------------------------------------------------- redaction


def test_redact_connection_strings():
    txt = (
        "DefaultEndpointsProtocol=https;AccountName=x;AccountKey=abc==;"
        "EndpointSuffix=core.windows.net Pwd=Secret123 sk-abcdef0123456789abcd"
    )
    out = redact_secrets(txt)
    assert "abc==" not in out
    assert "Secret123" not in out
    assert "sk-abcdef0123456789abcd" not in out


# ------------------------------------------------------------------- secret cache


def test_session_secret_cache(monkeypatch):
    calls = {"n": 0}

    def fake(label):
        calls["n"] += 1
        return "SECRET"

    monkeypatch.setattr("hemmy.utils.helpers.prompt_secret", fake)
    p = SessionSecretProvider()
    p("conn X")
    p("conn X")
    p("conn Y")
    assert calls["n"] == 2


# ---------------------------------------------------------------------- utilities


def test_schema_diff():
    src = [{"name": "id", "type": "int"}, {"name": "n", "type": "string"}]
    tgt = [{"name": "id", "type": "int"}, {"name": "age", "type": "int"}]
    diff = schema_diff(src, tgt)
    assert diff["only_in_source"] == ["n"]
    assert diff["only_in_target"] == ["age"]


# ------------------------------------------------------------ contesto persistente


def test_context_persists_across_turns():
    agent = _make_agent()
    seen = []

    def fake_call(messages):
        seen.append(json.dumps(messages, ensure_ascii=False))
        return "Final Answer: ok"

    agent._call_llm = fake_call
    agent.run("ricordati la parola ANANAS")
    agent.run("seconda domanda")
    assert "ANANAS" in seen[1]  # il 2o turno vede ancora il 1o


def test_run_handles_llm_error_gracefully():
    agent = _make_agent()

    def boom(messages):
        raise RuntimeError("Request timed out.")

    agent._call_llm = boom
    out = agent.run("ricontrolla la run")
    assert "Errore" in out or "errore" in out  # messaggio grazioso, niente crash
    assert len(agent.conversation) >= 2  # contesto preservato (system + domanda)


def test_reset_clears_context():
    agent = _make_agent()
    agent._call_llm = lambda m: "Final Answer: ok"
    agent.run("prima")
    agent.reset_conversation()
    assert len(agent.conversation) == 1  # solo il system


class _WriteGuard:
    """Guardrail-stub: marca l'azione come scrittura, senza chiedere approvazione."""

    def check(self, a, args):
        pass

    def is_write_action(self, a):
        return True


def test_anti_loop_guard_stops_repeated_write():
    calls = {"n": 0}

    def do(**kwargs):
        calls["n"] += 1
        return {"ok": True}

    agent = _make_agent(tools={"x.write": do}, guardrails=_WriteGuard())
    # Il modello insiste sulla STESSA scrittura+args a ogni iterazione.
    agent._call_llm = lambda m: "Action: x.write\nAction Input: {}"
    agent.run("fai la cosa")
    # Eseguita solo _max_repeat volte, poi la guardia blocca le ripetizioni.
    assert calls["n"] == agent._max_repeat
    convo = json.dumps(agent.conversation, ensure_ascii=False)
    assert "AZIONE RIPETUTA" in convo


def test_anti_loop_guard_does_not_block_repeated_reads():
    """Le LETTURE/polling identiche NON vengono bloccate (servono a seguire lo stato)."""
    calls = {"n": 0}

    def poll(**kwargs):
        calls["n"] += 1
        return {"status": "in_progress"}

    # _G di default: is_write_action -> False (lettura).
    agent = _make_agent(tools={"cicd.wait_for_run": poll})
    agent._call_llm = lambda m: 'Action: cicd.wait_for_run\nAction Input: {"run_id": "1"}'
    agent.run("segui la run")
    # Nessun blocco: eseguita a ogni iterazione fino al limite (max_iterations=5).
    assert calls["n"] == agent.config["max_iterations"]


def test_large_read_observation_not_truncated_at_4000():
    big = "y" * 20000  # file grande: col vecchio cap (4000) verrebbe tagliato
    agent = _make_agent(
        tools={"github.get_file": lambda **k: {"path": "environments/dev/main.tf", "content": big}}
    )
    calls = {"n": 0}

    def fake(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return 'Action: github.get_file\nAction Input: {"path": "environments/dev/main.tf"}'
        return "Final Answer: letto"

    agent._call_llm = fake
    agent.run("leggi environments/dev/main.tf")
    convo = json.dumps(agent.conversation, ensure_ascii=False)
    # Il contenuto completo del file sopravvive nell'Observation (nessun taglio a 4000).
    assert big in convo


def test_iteration_limit_returns_progress_summary():
    agent = _make_agent(tools={"sql.get_row_count": lambda **k: {"rows": 0}})
    i = {"n": 0}

    def fake(messages):
        i["n"] += 1
        # Args diversi a ogni giro: nessun Final Answer, la guardia anti-loop non scatta.
        return f'Action: sql.get_row_count\nAction Input: {{"table": "t{i["n"]}"}}'

    agent._call_llm = fake
    out = agent.run("conta le righe")
    assert "Riepilogo" in out and "iterazioni" in out
    assert "sql.get_row_count" in out  # il riepilogo elenca i passi svolti


# ---------------------------------------------------------------- IaC lint pre-commit


def test_iac_lint_files_clean():
    files = {"modules/rg/main.tf": 'resource "azurerm_resource_group" "this" {\n  name = var.name\n}\n'}
    res = iac_tools.lint_files(files)
    assert res["ok"] is True
    assert res["errors"] == []


def test_iac_lint_files_detects_placeholder():
    files = {"environments/dev/main.tf": 'module "<res2>" {\n  name = "rg"\n}\n'}
    res = iac_tools.lint_files(files)
    assert res["ok"] is False
    assert any("placeholder" in e for e in res["errors"])


def test_iac_lint_files_unbalanced_braces():
    files = {"x.tf": 'resource "a" "b" {\n  name = "x"\n'}
    res = iac_tools.lint_files(files)
    assert res["ok"] is False
    assert any("graffe" in e for e in res["errors"])


def test_iac_lint_files_count_known_after_apply_warning():
    files = {
        "modules/sql/main.tf": (
            'resource "azurerm_mssql_virtual_network_rule" "vnet" {\n'
            "  count     = var.subnet_id == null ? 0 : 1\n"
            "  subnet_id = var.subnet_id\n}\n"
        )
    }
    res = iac_tools.lint_files(files)
    assert res["ok"] is True  # è un warning, non un errore bloccante
    assert any("for_each" in w for w in res["warnings"])


def test_iac_lint_files_flags_ci_bandaids():
    files = {
        ".github/workflows/terraform-dev.yml": (
            "run: |\n"
            "  terraform state rm azurerm_monitor_diagnostic_setting.storage_blob\n"
            "  terraform apply -auto-approve -lock=false\n"
        )
    }
    res = iac_tools.lint_files(files)
    assert res["ok"] is True  # warning, non errore
    joined = " ".join(res["warnings"])
    assert "-lock=false" in joined
    assert "moved" in joined


# --------------------------------------------- Gate pre-commit (item 1a, deterministico)


def test_precommit_gate_blocks_bad_tf_commit():
    """github.commit_files di un .tf con placeholder residuo viene BLOCCATO dal gate:
    il tool NON viene eseguito e l'Observation spiega il blocco."""
    calls = {"n": 0}

    def commit(**kwargs):
        calls["n"] += 1
        return {"committed": True}

    agent = _make_agent(tools={"github.commit_files": commit}, guardrails=_WriteGuard())
    bad = 'module "<res2>" {\n  name = "rg"\n}\n'
    payload = json.dumps({"files": {"environments/dev/main.tf": bad}, "message": "x"})
    i = {"n": 0}

    def fake(messages):
        i["n"] += 1
        if i["n"] == 1:
            return f"Action: github.commit_files\nAction Input: {payload}"
        return "Final Answer: ok"

    agent._call_llm = fake
    agent.run("committa il modulo")
    assert calls["n"] == 0  # commit mai eseguito
    convo = json.dumps(agent.conversation, ensure_ascii=False)
    assert "COMMIT BLOCCATO" in convo


def test_precommit_gate_allows_clean_tf_commit():
    calls = {"n": 0}

    def commit(**kwargs):
        calls["n"] += 1
        return {"committed": True}

    agent = _make_agent(tools={"github.commit_files": commit}, guardrails=_WriteGuard())
    good = 'resource "azurerm_resource_group" "this" {\n  name = var.name\n}\n'
    payload = json.dumps({"files": {"environments/dev/main.tf": good}, "message": "x"})
    i = {"n": 0}

    def fake(messages):
        i["n"] += 1
        if i["n"] == 1:
            return f"Action: github.commit_files\nAction Input: {payload}"
        return "Final Answer: ok"

    agent._call_llm = fake
    agent.run("committa il modulo")
    assert calls["n"] == 1  # commit eseguito (nessun errore di lint)


# ------------------------------------------------- generate_moved (item 2, refactor state)


def test_generate_moved_basic():
    res = iac_tools.generate_moved(
        [
            {
                "from": "azurerm_monitor_diagnostic_setting.storage_blob",
                "to": "module.diag_storage_blob.azurerm_monitor_diagnostic_setting.this",
            }
        ]
    )
    content = res["content"]
    assert "moved {" in content
    assert "from = azurerm_monitor_diagnostic_setting.storage_blob" in content
    assert "to   = module.diag_storage_blob.azurerm_monitor_diagnostic_setting.this" in content
    # indirizzi come identificatori HCL, non stringhe (niente virgolette attorno)
    assert '"azurerm_monitor_diagnostic_setting' not in content
    assert res["moves_count"] == 1
    assert "moved.tf" in res["files"]


def test_generate_moved_validation():
    with pytest.raises(ValueError):
        iac_tools.generate_moved([])
    with pytest.raises(ValueError):
        iac_tools.generate_moved([{"from": "a"}])  # manca 'to'


# --------------------------------------------- Moduli observability / cost (item A)


def test_scaffold_module_log_analytics():
    res = iac_tools.scaffold_module("log_analytics", "log_analytics")
    main = res["files"]["modules/log_analytics/main.tf"]
    assert 'resource "azurerm_log_analytics_workspace" "this"' in main
    assert "retention_in_days" in main
    assert 'module "log_analytics"' in res["module_call"]


def test_scaffold_module_diagnostic_setting_all_categories():
    res = iac_tools.scaffold_module("diagnostic_setting", "diag_sql")
    main = res["files"]["modules/diag_sql/main.tf"]
    # best practice: cattura tutte le categorie via data source + dynamic block
    assert 'data "azurerm_monitor_diagnostic_categories" "this"' in main
    assert 'dynamic "enabled_log"' in main
    assert 'dynamic "metric"' in main
    assert "log_analytics_workspace_id" in main


def test_scaffold_module_action_group_and_metric_alert():
    ag = iac_tools.scaffold_module("action_group", "action_group")
    assert 'resource "azurerm_monitor_action_group" "this"' in ag["files"]["modules/action_group/main.tf"]
    al = iac_tools.scaffold_module("metric_alert", "metric_alert")
    main = al["files"]["modules/metric_alert/main.tf"]
    assert 'resource "azurerm_monitor_metric_alert" "this"' in main
    assert "action_group_id" in main


def test_scaffold_module_budget():
    res = iac_tools.scaffold_module("budget", "budget")
    main = res["files"]["modules/budget/main.tf"]
    assert 'resource "azurerm_consumption_budget_resource_group" "this"' in main
    assert 'dynamic "notification"' in main
    vars_tf = res["files"]["modules/budget/variables.tf"]
    assert "threshold_percentages" in vars_tf


def test_scaffold_module_still_rejects_unknown():
    with pytest.raises(ValueError):
        iac_tools.scaffold_module("cosmos", "x")


# --------------------------------------------- Remediation classificata per rischio (item B)


def test_remediation_low_risk_moved_block():
    res = remediation_tools.classify("moved_block")
    assert res["risk"] == "low"
    assert res["auto_apply_allowed"] is True
    assert res["requires_explicit_confirmation"] is False


def test_remediation_high_risk_state_rm_has_safer_alternative():
    res = remediation_tools.classify("state_rm", error="Invalid ...")
    assert res["risk"] == "high"
    assert res["requires_explicit_confirmation"] is True
    assert res["auto_apply_allowed"] is False
    assert "moved" in res["safer_alternative"]
    assert res["error"] == "Invalid ..."


def test_remediation_high_risk_apply_with_destroy():
    res = remediation_tools.classify("apply_with_destroy")
    assert res["risk"] == "high"
    assert res["requires_explicit_confirmation"] is True


def test_remediation_alias_and_unknown():
    # sinonimo → canonicalizzato
    assert remediation_tools.classify("rbac")["canonical"] == "rbac_assign"
    assert remediation_tools.classify("rbac")["risk"] == "high"
    # sconosciuto → default prudenziale medium, non auto-applicabile
    unknown = remediation_tools.classify("qualcosa_di_ignoto")
    assert unknown["known"] is False
    assert unknown["risk"] == "medium"
    assert unknown["auto_apply_allowed"] is False


# --------------------------------------------- Guardia destroy su iac.apply (item 1b)


def test_apply_blocks_unexpected_destroy(monkeypatch):
    monkeypatch.setattr(iac_tools.os.path, "exists", lambda p: True)
    monkeypatch.setattr(iac_tools, "plan_destroys", lambda wd: 3)
    with pytest.raises(RuntimeError) as e:
        iac_tools.apply("/tmp/infra")
    assert "APPLY BLOCCATO" in str(e.value)
    assert "confirm_destroy" in str(e.value)


def test_apply_allows_destroy_with_confirm(monkeypatch):
    monkeypatch.setattr(iac_tools.os.path, "exists", lambda p: True)
    monkeypatch.setattr(iac_tools, "plan_destroys", lambda wd: 3)
    called = {"n": 0}
    monkeypatch.setattr(iac_tools, "_run", lambda wd, args, timeout=600: called.__setitem__("n", 1) or {"stdout": "done"})
    iac_tools.apply("/tmp/infra", confirm_destroy=True)
    assert called["n"] == 1  # con conferma esplicita, l'apply procede


def test_iac_lint_files_intent_delta_public_access():
    files = {
        "modules/sql/main.tf": (
            'resource "azurerm_mssql_server" "this" {\n'
            "  public_network_access_enabled = true\n"
            '  start_ip_address = "0.0.0.0"\n}\n'
        )
    }
    res = iac_tools.lint_files(files)
    assert any("public access" in w.lower() or "public_network_access" in w for w in res["warnings"])


# ---------------------------------------------------------------------- audit log


def test_audit_records_write(tmp_path):
    audit = AuditLog(str(tmp_path / "audit.log"))
    audit.record("sql.execute_write", {"query": "INSERT INTO t VALUES (1)"}, "success")
    lines = (tmp_path / "audit.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["action"] == "sql.execute_write"
    assert entry["outcome"] == "success"
    assert "user" in entry and "timestamp" in entry


# ---------------------------------------------------------------------- IaC


def test_iac_write_file_ok(tmp_path):
    res = iac_tools.write_file(str(tmp_path), "main.tf", 'resource "x" "y" {}')
    assert res["written"] == "main.tf"
    assert (tmp_path / "main.tf").read_text(encoding="utf-8") == 'resource "x" "y" {}'


def test_iac_write_file_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        iac_tools.write_file(str(tmp_path), "../evil.tf", "x")


def test_iac_write_file_rejects_bad_extension(tmp_path):
    with pytest.raises(ValueError):
        iac_tools.write_file(str(tmp_path), "notes.txt", "x")


def test_iac_env_maps_arm_credentials(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "T")
    monkeypatch.setenv("AZURE_CLIENT_ID", "C")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "S")
    monkeypatch.setenv("ADF_SUBSCRIPTION_ID", "SUB")
    monkeypatch.delenv("ARM_TENANT_ID", raising=False)
    monkeypatch.delenv("ARM_SUBSCRIPTION_ID", raising=False)
    env = iac_tools._env()
    assert env["ARM_TENANT_ID"] == "T"
    assert env["ARM_CLIENT_ID"] == "C"
    assert env["ARM_SUBSCRIPTION_ID"] == "SUB"


def test_iac_apply_requires_plan(tmp_path):
    with pytest.raises(RuntimeError):
        iac_tools.apply(str(tmp_path))  # nessun tfplan presente


def test_iac_import_builds_command(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        iac_tools, "_run", lambda wd, args, timeout=600: captured.setdefault("args", args) or {"returncode": 0}
    )
    iac_tools.import_resource(
        "/wd", "azurerm_resource_group.rg", "/subscriptions/s/resourceGroups/rg"
    )
    assert captured["args"][0] == "import"
    assert captured["args"][-2:] == [
        "azurerm_resource_group.rg",
        "/subscriptions/s/resourceGroups/rg",
    ]


def test_move_resources_builds_target_id(monkeypatch):
    monkeypatch.setenv("ADF_SUBSCRIPTION_ID", "SUB")
    from hemmy.tools.resource import resource_tools

    captured = {}

    class _Resources:
        def begin_move_resources(self, src, params):
            captured["src"] = src
            captured["resources"] = params.resources
            captured["target"] = params.target_resource_group
            return type("P", (), {"result": lambda s: None})()

    class _Client:
        resources = _Resources()

    res = resource_tools.move_resources(_Client(), "rg-src", ["/rid/a"], "rg-dst")
    assert captured["target"] == "/subscriptions/SUB/resourceGroups/rg-dst"
    assert captured["resources"] == ["/rid/a"]
    assert res["moved"] == 1


def test_keyvault_url_building():
    assert keyvault_tools._vault_url("kv-ia") == "https://kv-ia.vault.azure.net"
    assert keyvault_tools._vault_url("https://x.vault.azure.net") == "https://x.vault.azure.net"


def test_keyvault_set_secret_cancelled():
    with pytest.raises(ValueError):
        keyvault_tools.set_secret(None, lambda label: "", "kv-ia", "sql-conn")


# -------------------------------------------------------------------- Networking


class _FakeNet:
    def __init__(self):
        vnet = type(
            "V",
            (),
            {
                "name": "vnet-data",
                "id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet-data",
                "location": "westeurope",
                "address_space": type("A", (), {"address_prefixes": ["10.0.0.0/16"]})(),
            },
        )()
        pe = type(
            "PE",
            (),
            {
                "name": "pe-sql",
                "subnet": type("S", (), {"id": "/sub/snet"})(),
                "private_link_service_connections": [
                    type(
                        "C",
                        (),
                        {
                            "name": "c1",
                            "private_link_service_id": "/rid/sql",
                            "group_ids": ["sqlServer"],
                        },
                    )()
                ],
            },
        )()

        class _VN:
            def list(self, rg):
                return [vnet]

            def list_all(self):
                return [vnet]

        class _PE:
            def list(self, rg):
                return [pe]

        self.virtual_networks = _VN()
        self.private_endpoints = _PE()


def test_network_list_vnets():
    res = network_tools.list_virtual_networks(_FakeNet(), "rg")
    assert res[0]["name"] == "vnet-data"
    assert res[0]["address_space"] == ["10.0.0.0/16"]
    assert res[0]["resource_group"] == "rg"


def test_network_list_private_endpoints():
    res = network_tools.list_private_endpoints(_FakeNet(), "rg")
    assert res[0]["name"] == "pe-sql"
    assert res[0]["connections"][0]["group_ids"] == ["sqlServer"]


def test_docs_networking_section(tmp_path):
    doc = _docstore(tmp_path)
    doc.state["networking"] = {
        "vnets": {"vnet-data": {"address_space": ["10.0.0.0/16"], "subnets": ["snet-adf"]}},
        "private_endpoints": [{"name": "pe-sql", "connections": [{"group_ids": ["sqlServer"]}]}],
        "nsgs": [{"name": "nsg-1"}],
    }
    md = doc.render_markdown()
    assert "## Networking" in md
    assert "vnet-data" in md and "pe-sql" in md
    assert "VNet: vnet-data" in md  # nodo mermaid


# ---------------------------------------------------------------- anonimizzazione


def test_anonymizer_roundtrip():
    from hemmy.security.anonymizer import Anonymizer

    a = Anonymizer(anonymize=True)
    alias = a.register("kviaagentwe01", "vault")
    assert alias.startswith("<vault")
    assert a.anonymize("il vault kviaagentwe01 è ok") == f"il vault {alias} è ok"
    assert a.deanonymize(f"il vault {alias} è ok") == "il vault kviaagentwe01 è ok"


def test_anonymizer_registers_from_result_and_ids():
    from hemmy.security.anonymizer import Anonymizer

    a = Anonymizer()
    a.register_from_result(
        {
            "name": "adf-ia-agent",
            "id": "/subscriptions/x/resourceGroups/rg-we-aura-01/providers/Microsoft.DataFactory/factories/adf-ia-agent",
            "location": "westeurope",
        }
    )
    # nome e resource group registrati; 'location' NO
    assert "adf-ia-agent" in a._real_to_alias
    assert "rg-we-aura-01" in a._real_to_alias
    assert "westeurope" not in a._real_to_alias


def test_anonymizer_deanonymize_obj_args():
    from hemmy.security.anonymizer import Anonymizer

    a = Anonymizer()
    alias = a.register("kv-prod-we01", "vault")
    args = {"name": alias, "nested": [alias]}
    out = a.deanonymize_obj(args)
    assert out["name"] == "kv-prod-we01"
    assert out["nested"] == ["kv-prod-we01"]


def test_agent_hides_real_names_from_llm_and_restores_answer():
    from hemmy.security.anonymizer import Anonymizer

    agent = _make_agent()
    agent.anonymizer = Anonymizer(anonymize=True)
    alias = agent.anonymizer.register("kviaagentwe01", "vault")

    seen = {}

    def fake_call(messages):
        seen["msgs"] = json.dumps(messages, ensure_ascii=False)
        return f"Final Answer: Il vault {alias} è raggiungibile"

    agent._call_llm = fake_call
    answer = agent.run("com'è messo il vault kviaagentwe01?")

    # il modello NON ha visto il nome reale
    assert "kviaagentwe01" not in seen["msgs"]
    assert alias in seen["msgs"]
    # la risposta all'utente riporta il nome reale
    assert "kviaagentwe01" in answer


# ---------------------------------------------------------------- autenticazione


def test_credential_user_mode_is_personal(monkeypatch):
    monkeypatch.setenv("AZURE_AUTH_MODE", "user")
    from hemmy.infra.clients import _credential

    cred = _credential()
    # identità personale: NON è un ClientSecretCredential
    from azure.identity import ClientSecretCredential

    assert not isinstance(cred, ClientSecretCredential)
    assert hasattr(cred, "get_token")


def test_credential_service_principal_mode(monkeypatch):
    monkeypatch.setenv("AZURE_AUTH_MODE", "service_principal")
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")
    from hemmy.infra.clients import _credential
    from azure.identity import ClientSecretCredential

    assert isinstance(_credential(), ClientSecretCredential)


# ------------------------------------------------------------------- lazy client


def test_lazy_client_defers_construction():
    from hemmy.infra.clients import LazyClient

    built = {"n": 0}

    class Real:
        def ping(self):
            return "pong"

    def factory():
        built["n"] += 1
        return Real()

    lc = LazyClient(factory)
    assert built["n"] == 0  # nessuna connessione all'avvio
    assert lc.ping() == "pong"  # costruito solo al primo uso
    assert built["n"] == 1
    lc.ping()
    assert built["n"] == 1  # client riusato (non ricostruito)


# ---------------------------------------------------------------------- CI/CD


def test_cicd_generate_github_per_env(tmp_path):
    cfg = {"provider": "github", "iac_dir": "infra"}
    res = cicd_tools.generate_pipeline(cfg, str(tmp_path))
    assert res["provider"] == "github"
    # una pipeline dedicata per ambiente (dev/uat/prod)
    for env in ("dev", "uat", "prod"):
        content = (tmp_path / ".github" / "workflows" / f"terraform-{env}.yml").read_text(encoding="utf-8")
        assert "workflow_dispatch" in content
        assert f"working-directory: environments/{env}" in content
        assert "terraform ${{ inputs.command }}" in content


def test_cicd_generate_github_single_env(tmp_path):
    cfg = {"provider": "github", "iac_dir": "infra"}
    res = cicd_tools.generate_pipeline(cfg, str(tmp_path), environment="dev")
    assert res["written"] == [".github/workflows/terraform-dev.yml"]
    assert not (tmp_path / ".github" / "workflows" / "terraform-uat.yml").exists()


def test_cicd_generate_azure_devops(tmp_path):
    cfg = {"provider": "azure_devops", "iac_dir": "infra"}
    res = cicd_tools.generate_pipeline(cfg, str(tmp_path))
    assert res["provider"] == "azure_devops"
    content = (tmp_path / "azure-pipelines-dev.yml").read_text(encoding="utf-8")
    assert "parameters:" in content and "TerraformInstaller@1" in content


def test_cicd_generate_bad_provider(tmp_path):
    with pytest.raises(ValueError):
        cicd_tools.generate_pipeline({"provider": "jenkins"}, str(tmp_path))


def test_cicd_trigger_github_payload_per_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "TKN")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    captured = {}

    def fake_http(method, url, headers, body=None):
        captured["url"] = url
        captured["body"] = body
        return 204, {}

    monkeypatch.setattr(cicd_tools, "_http", fake_http)
    cfg = {"provider": "github", "github": {"repo": "me/repo", "branch": "main"}}
    res = cicd_tools.trigger_pipeline(cfg, "apply", environment="uat")
    assert res["command"] == "apply"
    assert res["environment"] == "uat"
    assert "me/repo/actions/workflows/terraform-uat.yml/dispatches" in captured["url"]
    assert captured["body"]["inputs"]["command"] == "apply"
    assert captured["body"]["ref"] == "main"


def test_cicd_trigger_bad_environment(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "TKN")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    cfg = {"provider": "github", "github": {"repo": "me/repo"}}
    with pytest.raises(ValueError):
        cicd_tools.trigger_pipeline(cfg, "plan", environment="staging")


def test_cicd_render_pipelines_all_envs():
    rendered = cicd_tools.render_pipelines({"provider": "github"})
    assert set(rendered) == {
        ".github/workflows/terraform-dev.yml",
        ".github/workflows/terraform-uat.yml",
        ".github/workflows/terraform-prod.yml",
    }


def test_cicd_github_workflow_best_practices():
    content = cicd_tools._github_workflow("dev")
    # serializza le run (lock/lease dello state)
    assert "concurrency:" in content
    assert "group: terraform-dev" in content
    assert "cancel-in-progress: false" in content
    # timeout esplicito: apply appesa fallisce con log
    assert "timeout-minutes: 30" in content
    # validate prima del comando
    assert "terraform validate" in content
    # NESSUN band-aid nel workflow generato
    assert "-lock=false" not in content
    assert "state rm" not in content
    assert "terraform import" not in content


def test_cicd_azuredevops_workflow_timeout():
    content = cicd_tools._azuredevops_pipeline("prod")
    assert "timeoutInMinutes: 30" in content
    assert "terraform validate" in content
    assert "-lock=false" not in content


def test_cicd_trigger_bad_command():
    with pytest.raises(ValueError):
        cicd_tools.trigger_pipeline({"provider": "github"}, "nuke")


def test_cicd_missing_github_config(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    with pytest.raises(ValueError):
        cicd_tools.trigger_pipeline({"provider": "github"}, "plan")


def test_iac_scaffold_storage():
    res = iac_tools.scaffold("storage_account", "sa", {"account_name": "mystorage", "location": "westeurope"})
    assert 'resource "azurerm_storage_account" "sa"' in res["hcl"]
    assert "mystorage" in res["hcl"]


def test_iac_scaffold_bad_kind():
    with pytest.raises(ValueError):
        iac_tools.scaffold("cosmos_db", "x")


def test_iac_write_file_allows_subdir(tmp_path):
    res = iac_tools.write_file(str(tmp_path), "modules/rg/main.tf", 'resource "x" "y" {}')
    assert (tmp_path / "modules" / "rg" / "main.tf").exists()
    assert res["written"] == "modules/rg/main.tf"


def test_iac_scaffold_module_storage():
    res = iac_tools.scaffold_module("storage_account", "sa_demo", {"name": "mystorage"})
    files = res["files"]
    assert "modules/sa_demo/main.tf" in files
    assert "modules/sa_demo/variables.tf" in files
    assert "modules/sa_demo/outputs.tf" in files
    assert 'resource "azurerm_storage_account" "this"' in files["modules/sa_demo/main.tf"]
    assert 'variable "account_tier"' in files["modules/sa_demo/variables.tf"]
    assert 'output "primary_blob_endpoint"' in files["modules/sa_demo/outputs.tf"]
    assert 'module "sa_demo"' in res["module_call"]
    assert 'source = "../../modules/sa_demo"' in res["module_call"]


def test_iac_scaffold_module_bad_kind():
    with pytest.raises(ValueError):
        iac_tools.scaffold_module("cosmos", "x")


def test_iac_scaffold_module_storage_secure_defaults():
    res = iac_tools.scaffold_module("storage_account", "sa_demo")
    main = res["files"]["modules/sa_demo/main.tf"]
    variables = res["files"]["modules/sa_demo/variables.tf"]
    # best practice by default
    assert 'min_tls_version' in main and '"TLS1_2"' in main
    assert "allow_nested_items_to_be_public = false" in main
    assert "public_network_access_enabled" in main
    assert 'variable "public_network_access_enabled"' in variables
    assert "default = false" in variables


def test_iac_scaffold_module_key_vault_secure_defaults():
    res = iac_tools.scaffold_module("key_vault", "kv_demo")
    main = res["files"]["modules/kv_demo/main.tf"]
    assert "enable_rbac_authorization" in main and "true" in main
    assert "purge_protection_enabled" in main
    assert "soft_delete_retention_days" in main
    assert "public_network_access_enabled" in main


def test_iac_scaffold_module_sql_database_safe_sku():
    res = iac_tools.scaffold_module("sql_database", "db_demo")
    variables = res["files"]["modules/db_demo/variables.tf"]
    # SKU serverless GP_S_Gen5_1 puo' fallire il provisioning: default sicuro S0
    assert '"S0"' in variables
    assert "GP_S_Gen5_1" not in variables


def test_iac_scaffold_environment_best_practices():
    res = iac_tools.scaffold_environment("dev", storage_account="sa", container="tfstate")
    files = res["files"]
    # required_version serve per i blocchi moved/import
    assert 'required_version = ">= 1.5.0"' in files["environments/dev/providers.tf"]
    # guida al refactor pulito con moved, non con band-aid
    main = files["environments/dev/main.tf"]
    assert "moved" in main
    assert "state rm" in main  # citato come da NON usare


def test_iac_scaffold_environment():
    res = iac_tools.scaffold_environment("dev", storage_account="sa", container="tfstate")
    files = res["files"]
    for f in ("providers.tf", "backend.tf", "locals.tf", "variables.tf", "main.tf", "terraform.tfvars"):
        assert f"environments/dev/{f}" in files
    assert 'key                  = "dev/terraform.tfstate"' in files["environments/dev/backend.tf"]
    assert 'environment = "dev"' in files["environments/dev/locals.tf"]
    assert "azurerm" in files["environments/dev/providers.tf"]


def test_iac_backend_hcl():
    hcl = iac_tools._backend_hcl("rg", "sa", "tfstate", "prod.tfstate")
    assert 'backend "azurerm"' in hcl
    assert 'storage_account_name = "sa"' in hcl
    assert 'key                  = "prod.tfstate"' in hcl
    assert "use_azuread_auth     = true" in hcl


def test_configure_remote_backend_runs_migrate(tmp_path, monkeypatch):
    captured = {}

    def fake_run(wd, args, timeout=600):
        captured["args"] = args
        return {"returncode": 0}

    monkeypatch.setattr(iac_tools, "_run", fake_run)
    res = iac_tools.configure_remote_backend(str(tmp_path), "rg", "sa", "tfstate")
    assert (tmp_path / "backend.tf").exists()
    assert "-migrate-state" in captured["args"]
    assert res["backend"]["storage_account"] == "sa"


# ------------------------------------------------------------------------ Redaction


def test_redact_github_token():
    out = redact_secrets("remote https://x-access-token:github_pat_SECRET@github.com/me/repo.git")
    assert "github_pat_SECRET" not in out


# ------------------------------------------------------- GitHub scrittura diretta


def test_github_commit_files_sequence(monkeypatch):
    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_X")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    calls = []

    def fake_http(method, url, headers, body=None):
        calls.append((method, url, body))
        if url.endswith("/ref/heads/main"):
            return 200, {"object": {"sha": "BASE"}}
        if "/commits/BASE" in url:
            return 200, {"tree": {"sha": "BASETREE"}}
        if url.endswith("/trees"):
            return 201, {"sha": "NEWTREE"}
        if url.endswith("/commits"):
            return 201, {"sha": "NEWCOMMIT"}
        if url.endswith("/refs/heads/main"):
            return 200, {}
        return 200, {}

    monkeypatch.setattr(github_tools, "_http", fake_http)
    res = github_tools.commit_files({"infra/rg.tf": 'resource "x" "y" {}'}, "add rg", "main")
    assert res["commit_sha"] == "NEWCOMMIT"
    assert res["committed"] == ["infra/rg.tf"]
    # verifica: tree con base_tree, commit con parent BASE, ref aggiornato a NEWCOMMIT
    tree_call = next(c for c in calls if c[1].endswith("/trees"))
    assert tree_call[2]["base_tree"] == "BASETREE"
    commit_call = next(c for c in calls if c[1].endswith("/git/commits"))
    assert commit_call[2]["parents"] == ["BASE"] and commit_call[2]["tree"] == "NEWTREE"
    ref_call = next(c for c in calls if c[1].endswith("/refs/heads/main"))
    assert ref_call[2]["sha"] == "NEWCOMMIT"


def test_github_commit_files_empty_repo_bootstrap(monkeypatch):
    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_X")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    puts = []

    def fake_http(method, url, headers, body=None):
        if url.endswith("/ref/heads/main"):
            raise RuntimeError("HTTP 409: Git Repository is empty.")
        if method == "PUT" and "/contents/" in url:
            puts.append(url)
            return 201, {"commit": {"sha": "C1"}}
        return 200, {}

    monkeypatch.setattr(github_tools, "_http", fake_http)
    res = github_tools.commit_files({"infra/rg.tf": "x", "README.md": "y"}, "init", "main")
    assert res["bootstrap"] is True
    assert res["committed"] == ["infra/rg.tf", "README.md"]
    assert any("/contents/infra/rg.tf" in u for u in puts)


def test_github_set_secret_encrypts_value(monkeypatch):
    from nacl import encoding, public

    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")

    # Coppia di chiavi di test: la pubblica va all'endpoint, con la privata decifriamo.
    priv = public.PrivateKey.generate()
    pub_b64 = priv.public_key.encode(encoding.Base64Encoder()).decode()
    captured = {}

    def fake_http(method, url, headers, body=None):
        if url.endswith("/public-key"):
            return 200, {"key": pub_b64, "key_id": "KID"}
        if method == "PUT" and "/actions/secrets/ARM_CLIENT_ID" in url:
            captured["body"] = body
            return 201, {}
        return 200, {}

    monkeypatch.setattr(github_tools, "_http", fake_http)
    res = github_tools.set_secret(lambda label: "super-secret-value", "ARM_CLIENT_ID")
    assert res["secret"] == "ARM_CLIENT_ID"
    assert captured["body"]["key_id"] == "KID"
    # il valore è cifrato (sealed box) e decifrabile solo con la chiave privata
    import base64 as _b64

    enc = _b64.b64decode(captured["body"]["encrypted_value"])
    dec = public.SealedBox(priv).decrypt(enc).decode()
    assert dec == "super-secret-value"


def test_github_set_secret_cancelled(monkeypatch):
    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    monkeypatch.setattr(github_tools, "_http", lambda *a, **k: (200, {"key": "x", "key_id": "k"}))
    with pytest.raises(ValueError):
        github_tools.set_secret(lambda label: "", "ARM_CLIENT_ID")


def test_iac_sql_server_module_uses_keyvault():
    res = iac_tools.scaffold_module("sql_server", "sql_demo")
    main = res["files"]["modules/sql_demo/main.tf"]
    assert 'data "azurerm_key_vault_secret" "admin_password"' in main
    assert "data.azurerm_key_vault_secret.admin_password.value" in main
    # la password proviene solo da Key Vault (nessun valore in chiaro nel modulo)
    assert 'variable "key_vault_id"' in res["files"]["modules/sql_demo/variables.tf"]
    assert "var.key_vault_id" in main  # referenziato nel data source


def test_iac_flat_sql_server_no_plaintext_password():
    res = iac_tools.scaffold("sql_server", "sql1", {"key_vault_id": "azurerm_key_vault.kv.id"})
    assert "azurerm_key_vault_secret" in res["hcl"]
    assert "var.sql_admin_password" not in res["hcl"]  # niente più password come variabile


def test_github_commit_files_requires_token(monkeypatch):
    from hemmy.tools.github import github_tools

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    with pytest.raises(ValueError):
        github_tools.commit_files({"a.tf": "x"}, "msg")


# ------------------------------------------------------- GitHub lettura (read)


def test_github_get_file_decodes_base64(monkeypatch):
    import base64 as _b64

    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    content = 'resource "azurerm_mssql_server" "this" {}\n'
    encoded = _b64.b64encode(content.encode()).decode()

    def fake_http(method, url, headers, body=None):
        assert method == "GET"
        assert "/contents/modules/sql_server/main.tf" in url
        return 200, {"encoding": "base64", "content": encoded, "sha": "S1", "size": len(content)}

    monkeypatch.setattr(github_tools, "_http", fake_http)
    res = github_tools.get_file("modules/sql_server/main.tf")
    assert res["content"] == content
    assert res["sha"] == "S1"


def test_github_get_file_on_directory_raises(monkeypatch):
    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    monkeypatch.setattr(github_tools, "_http", lambda *a, **k: (200, [{"name": "main.tf"}]))
    with pytest.raises(ValueError):
        github_tools.get_file("modules/sql_server")


def test_github_list_directory(monkeypatch):
    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    monkeypatch.setattr(
        github_tools,
        "_http",
        lambda *a, **k: (
            200,
            [
                {"name": "main.tf", "path": "modules/x/main.tf", "type": "file", "size": 10},
                {"name": "sub", "path": "modules/x/sub", "type": "dir", "size": 0},
            ],
        ),
    )
    res = github_tools.list_directory("modules/x")
    assert [e["name"] for e in res] == ["main.tf", "sub"]
    assert res[1]["type"] == "dir"


def test_github_get_tree(monkeypatch):
    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")

    def fake_http(method, url, headers, body=None):
        assert "recursive=1" in url
        return 200, {"truncated": False, "tree": [{"path": "environments/dev/main.tf", "type": "blob"}]}

    monkeypatch.setattr(github_tools, "_http", fake_http)
    res = github_tools.get_tree(ref="main")
    assert res["tree"][0]["path"] == "environments/dev/main.tf"
    assert res["truncated"] is False


def test_github_get_latest_run(monkeypatch):
    from hemmy.tools.github import github_tools

    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    monkeypatch.setattr(
        github_tools,
        "_http",
        lambda *a, **k: (
            200,
            {"workflow_runs": [{"id": 42, "name": "Terraform dev", "status": "completed", "conclusion": "failure"}]},
        ),
    )
    res = github_tools.get_latest_run("terraform-dev.yml")
    assert res["run"]["id"] == 42
    assert res["run"]["conclusion"] == "failure"


def test_cicd_render_pipeline_github():
    rendered = cicd_tools.render_pipeline({"provider": "github", "iac_dir": "infra"}, environment="dev")
    assert rendered["path"] == ".github/workflows/terraform-dev.yml"
    assert "workflow_dispatch" in rendered["content"]


# ------------------------------------------------------------ naming convention


def _naming_cfg():
    return {
        "abbreviations": {
            "resource_group": "rg",
            "storage_account": "sa",
            "data_factory": "adf",
        },
        "regions": {"westeurope": "we"},
        "environments": {"dev": "dev", "uat": "uat", "prod": "prod"},
        "no_hyphen_types": ["storage_account"],
        "max_len": {"storage_account": 24},
        "pattern": "{abbr}-{workload}-{env}-{region}-{instance}",
    }


def test_naming_build_hyphen():
    from hemmy.tools.naming import naming_tools

    res = naming_tools.build(_naming_cfg(), "data_factory", "iaagent", "dev")
    assert res["name"] == "adf-iaagent-dev-we-01"


def test_naming_build_storage_no_hyphen_and_maxlen():
    from hemmy.tools.naming import naming_tools

    res = naming_tools.build(_naming_cfg(), "storage_account", "iaagent", "dev")
    assert res["name"] == "saiaagentdevwe01"
    assert "-" not in res["name"]
    assert len(res["name"]) <= 24


def test_naming_build_unknown_type():
    from hemmy.tools.naming import naming_tools

    with pytest.raises(ValueError):
        naming_tools.build(_naming_cfg(), "cosmos_db", "iaagent", "dev")


# ---------------------------------------------------- feasibility analysis (cervello)


def test_feasibility_all_in_one_subnet_not_literal_for_adf_and_log():
    from hemmy.tools.feasibility import feasibility_tools

    res = feasibility_tools.check(
        ["storage_account", "key_vault", "sql_server", "data_factory", "log_analytics"],
        networking="all_in_one_subnet",
    )
    # la richiesta letterale "tutto in una subnet" NON è realizzabile per ogni risorsa
    assert res["literally_achievable"] is False
    by = {e["canonical"]: e for e in res["per_resource"]}
    assert by["data_factory"]["achievable_literally"] is False
    assert by["log_analytics"]["achievable_literally"] is False
    # e viene proposto il modello corretto per risorsa
    assert "Managed VNet" in by["data_factory"]["network_model"]
    assert "AMPLS" in by["log_analytics"]["network_model"]


def test_feasibility_vnet_only_storage_kv_achievable():
    from hemmy.tools.feasibility import feasibility_tools

    res = feasibility_tools.check(["storage_account", "key_vault"], networking="vnet_only")
    by = {e["canonical"]: e for e in res["per_resource"]}
    assert by["storage_account"]["achievable"] is True
    assert by["key_vault"]["achievable"] is True
    # warning intent↔implementazione su bypass=AzureServices
    assert any("bypass" in w for w in res["warnings"])


def test_feasibility_flags_sql_public_access_delta():
    from hemmy.tools.feasibility import feasibility_tools

    res = feasibility_tools.check(["sql_server"], networking="vnet_only")
    assert any("public_network_access_enabled=false" in w for w in res["warnings"])


def test_feasibility_unknown_resource():
    from hemmy.tools.feasibility import feasibility_tools

    res = feasibility_tools.check(["cosmos_db"], networking="vnet_only")
    assert res["per_resource"][0]["known"] is False
    assert res["literally_achievable"] is False


def test_feasibility_with_existing_state_action_needed():
    """Con lo stato reale osservato, ogni risorsa riporta action_needed concreto (item 3)."""
    from hemmy.tools.feasibility import feasibility_tools

    res = feasibility_tools.check(
        ["data_factory", "storage_account"],
        networking="vnet_only",
        existing={
            "data_factory": {"managed_vnet_enabled": False},
            "storage_account": {"private_endpoint_exists": True},
        },
    )
    by = {e["canonical"]: e for e in res["per_resource"]}
    assert "Managed VNet" in by["data_factory"]["action_needed"]
    assert by["data_factory"]["observed"] == {"managed_vnet_enabled": False}
    assert "nessuna azione" in by["storage_account"]["action_needed"].lower()


def test_feasibility_existing_flags_public_access_still_on():
    from hemmy.tools.feasibility import feasibility_tools

    res = feasibility_tools.check(
        ["storage_account"],
        networking="vnet_only",
        existing={"storage_account": {"public_network_access_enabled": True}},
    )
    entry = res["per_resource"][0]
    assert "public" in entry["action_needed"].lower()


# ------------------------------------------------ GitHub read tools (tool surface)


def test_github_get_commit(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")

    def fake_http(method, url, headers, body=None):
        assert "/commits/abc123" in url
        return 200, {
            "sha": "abc123",
            "commit": {"message": "feat: x", "author": {"name": "cc", "date": "2026-01-01"}},
            "files": [{"filename": "infra/main.tf", "status": "modified", "additions": 3, "deletions": 1}],
        }

    monkeypatch.setattr(github_tools, "_http", fake_http)
    res = github_tools.get_commit("abc123")
    assert res["sha"] == "abc123"
    assert res["message"] == "feat: x"
    assert res["files"][0]["filename"] == "infra/main.tf"


def test_github_get_diff(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")

    def fake_http(method, url, headers, body=None):
        assert "/compare/main...feature" in url
        return 200, {
            "status": "ahead",
            "ahead_by": 2,
            "behind_by": 0,
            "total_commits": 2,
            "files": [{"filename": "infra/x.tf", "status": "added", "additions": 5, "deletions": 0, "patch": "@@"}],
        }

    monkeypatch.setattr(github_tools, "_http", fake_http)
    res = github_tools.get_diff("main", "feature")
    assert res["ahead_by"] == 2
    assert res["files"][0]["patch"] == "@@"


def test_github_get_workflow_list(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")

    def fake_http(method, url, headers, body=None):
        assert url.endswith("/actions/workflows")
        return 200, {"workflows": [{"id": 1, "name": "Terraform dev", "path": ".github/workflows/terraform-dev.yml", "state": "active"}]}

    monkeypatch.setattr(github_tools, "_http", fake_http)
    res = github_tools.get_workflow()
    assert res["workflows"][0]["name"] == "Terraform dev"


# ---------------------------------------------------------- OIDC (item 4)


def test_cicd_workflow_oidc_no_client_secret():
    secret_wf = cicd_tools._github_workflow("dev", auth="secret")
    oidc_wf = cicd_tools._github_workflow("dev", auth="oidc")
    # secret mode: usa ARM_CLIENT_SECRET; oidc mode: NON lo usa e fa azure/login
    assert "ARM_CLIENT_SECRET" in secret_wf
    assert "ARM_CLIENT_SECRET" not in oidc_wf
    assert "azure/login@v2" in oidc_wf
    assert "ARM_USE_OIDC" in oidc_wf
    assert "id-token: write" in oidc_wf


def test_cicd_render_pipelines_respects_auth_oidc():
    files = cicd_tools.render_pipelines({"provider": "github", "auth": "oidc"})
    dev = files[".github/workflows/terraform-dev.yml"]
    assert "ARM_CLIENT_SECRET" not in dev
    assert "azure/login@v2" in dev


def test_generate_oidc_federation():
    res = cicd_tools.generate_oidc_federation(
        app_id="00000000-app", environments=["dev", "prod"], branch="main", repo="me/repo"
    )
    subjects = [c["subject"] for c in res["federated_credentials"]]
    assert "repo:me/repo:environment:dev" in subjects
    assert "repo:me/repo:environment:prod" in subjects
    assert "repo:me/repo:ref:refs/heads/main" in subjects
    assert all(c["issuer"] == "https://token.actions.githubusercontent.com" for c in res["federated_credentials"])
    assert any("az ad app federated-credential create --id 00000000-app" in cmd for cmd in res["az_commands"])


def test_generate_oidc_federation_requires_repo(monkeypatch):
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    with pytest.raises(ValueError):
        cicd_tools.generate_oidc_federation(repo=None)


def test_cicd_wait_for_run(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")
    monkeypatch.setattr(cicd_tools.time, "sleep", lambda s: None)
    seq = iter([("in_progress", None), ("completed", "success")])

    def fake_http(method, url, headers, body=None):
        st, cc = next(seq)
        return 200, {"status": st, "conclusion": cc}

    monkeypatch.setattr(cicd_tools, "_http", fake_http)
    res = cicd_tools.wait_for_run({"provider": "github"}, "123", poll_interval=0)
    assert res["status"] == "completed" and res["conclusion"] == "success"
    assert res["timed_out"] is False


def test_cicd_get_run_logs_identifies_failure_and_redacts(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")

    def fake_http(method, url, headers, body=None):
        # /jobs → un job con step 'terraform init' fallito
        return 200, {
            "jobs": [
                {
                    "id": 99,
                    "name": "terraform",
                    "conclusion": "failure",
                    "steps": [
                        {"name": "terraform init", "conclusion": "failure"},
                        {"name": "checkout", "conclusion": "success"},
                    ],
                }
            ]
        }

    def fake_download(url, headers):
        # nessun header di auth deve finire nella richiesta all'URL firmato
        return b"Error: building ARM Config: Password=Secret123 client_secret is empty"

    monkeypatch.setattr(cicd_tools, "_http", fake_http)
    monkeypatch.setattr(cicd_tools, "_download_text", fake_download)
    res = cicd_tools.get_run_logs({"provider": "github"}, "35148787928")
    assert res["failed"][0]["job"] == "terraform"
    assert "terraform init" in res["failed"][0]["failed_steps"]
    assert "building ARM Config" in res["logs"]
    assert "Secret123" not in res["logs"]  # redaction attiva sui log


# ------------------------------------------------------------------ Management Lock


def test_lock_invalid_level():
    with pytest.raises(ValueError):
        lock_tools.create_lock(object(), "rg", "lk", level="Bogus")


def test_lock_list():
    class _Client:
        class management_locks:
            @staticmethod
            def list_at_resource_group_level(rg):
                return [
                    type("L", (), {"name": "lk-prod", "level": "CanNotDelete", "notes": "prod", "id": "/id"})()
                ]

    res = lock_tools.list_locks(_Client(), "rg")
    assert res[0]["name"] == "lk-prod"
    assert res[0]["level"] == "CanNotDelete"


# ---------------------------------------------------------------------- RBAC


class _FakeAuth:
    """Client RBAC fittizio per testare assign_role senza Azure."""

    def __init__(self):
        self.created = []

        class _RoleDefs:
            def list(self, scope, filter):
                return [type("RD", (), {"id": "/rd/secrets-officer"})()]

        class _RoleAssign:
            def __init__(self, outer):
                self.outer = outer

            def create(self, scope, name, params):
                self.outer.created.append((scope, name, params.principal_id, params.role_definition_id))
                return type("A", (), {"name": name})()

            def list_for_scope(self, scope):
                return []

        self.role_definitions = _RoleDefs()
        self.role_assignments = _RoleAssign(self)


def test_rbac_assign_role_resolves_and_creates():
    client = _FakeAuth()
    res = rbac_tools.assign_role(
        client, "/subscriptions/x/resourceGroups/rg", "PID-123", "Key Vault Secrets Officer"
    )
    assert res["assigned_role"] == "Key Vault Secrets Officer"
    assert res["principal_id"] == "PID-123"
    assert client.created and client.created[0][2] == "PID-123"
    assert client.created[0][3] == "/rd/secrets-officer"


def test_rbac_role_not_found():
    class Empty:
        class role_definitions:
            @staticmethod
            def list(scope, filter):
                return []

    with pytest.raises(ValueError):
        rbac_tools.assign_role(Empty(), "/scope", "PID", "Ruolo Inesistente")


def test_audit_redacts_secrets(tmp_path):
    audit = AuditLog(str(tmp_path / "audit.log"))
    audit.record("blob.upload", {"content": "AccountKey=supersecret=="}, "success")
    text = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "supersecret" not in text


# ------------------------------------------------------- documentazione vivente


def _docstore(tmp_path):
    return DocStore(
        str(tmp_path / "state.json"),
        str(tmp_path / "INFRA.md"),
        default_account="staiagentwe01",
        factory_name="adf-ia-agent",
    )


def test_docs_track_current_state(tmp_path):
    doc = _docstore(tmp_path)
    assert doc.apply("blob.create_container", {"container": "iaagentcontainer"})
    assert doc.apply(
        "adf.create_linked_service",
        {"name": "LS_Blob", "service_type": "AzureBlobStorage"},
    )
    assert doc.apply(
        "adf.create_copy_pipeline",
        {
            "pipeline_name": "pipeline_test",
            "source_linked_service": "LS_Blob",
            "source_container": "iaagentcontainer",
            "source_file": "f.xlsx",
            "sink_linked_service": "LS_Sql",
            "sink_table": "testiaagent",
            "source_format": "Excel",
        },
        {"source_dataset": "DS_src", "sink_dataset": "DS_sink"},
    )
    doc.save()
    md = (tmp_path / "INFRA.md").read_text(encoding="utf-8")
    assert "iaagentcontainer" in md
    assert "pipeline_test" in md
    assert "```mermaid" in md
    assert "LS_Blob" in md


def test_docs_reflect_rename_not_history(tmp_path):
    doc = _docstore(tmp_path)
    doc.apply("sql.execute_write", {"query": "CREATE TABLE dbo.old (id INT)"})
    doc.apply("sql.execute_write", {"query": "EXEC sp_rename 'dbo.old', 'new';"})
    tables = doc.state["sql_tables"]["default"]
    assert "dbo.new" in tables and "dbo.old" not in tables  # solo lo stato attuale


def test_docs_delete_removes_resource(tmp_path):
    doc = _docstore(tmp_path)
    doc.apply("queue.create_queue", {"queue_name": "q1"})
    doc.apply("queue.delete_queue", {"queue_name": "q1"})
    assert "q1" not in doc.state["storage"]["staiagentwe01"]["queues"]


def test_docs_adf_delete_removes_from_state(tmp_path):
    doc = _docstore(tmp_path)
    doc.apply("adf.create_linked_service", {"name": "LS_orphan", "service_type": "AzureBlobStorage"})
    assert "LS_orphan" in doc.state["adf"]["linked_services"]
    doc.apply("adf.delete_linked_service", {"name": "LS_orphan"})
    assert "LS_orphan" not in doc.state["adf"]["linked_services"]


def test_docs_custom_note_persists_through_sync(tmp_path):
    doc = _docstore(tmp_path)
    doc.add_note("Runbook", "Per rieseguire la pipeline: usa adf.run_and_wait.")
    doc.apply("blob.create_container", {"container": "c1"})
    doc.save()
    md = (tmp_path / "INFRA.md").read_text(encoding="utf-8")
    assert "Runbook" in md and "adf.run_and_wait" in md

    # docs.sync ricostruisce l'inventario ma le note custom sopravvivono.
    doc.reset_state()
    doc.save()
    md2 = (tmp_path / "INFRA.md").read_text(encoding="utf-8")
    assert "Runbook" in md2  # nota preservata
    assert "c1" not in md2   # inventario azzerato dal reset


# ----------------------------------------------------- UI web (interfaces/web.py)


def test_web_approval_roundtrip():
    """La callback di approvazione emette l'evento e attende la risposta dal browser."""
    import threading
    from hemmy.interfaces import web

    s = web._WebSession()
    res = {}
    t = threading.Thread(
        target=lambda: res.__setitem__("ok", s.approval_fn("iac.apply", {"x": 1}, "Plan: 1 to add"))
    )
    t.start()
    ev = s.events.get(timeout=2)
    assert ev["type"] == "approval_request" and ev["action"] == "iac.apply" and ev["preview"]
    s.answer.put(True)
    t.join(2)
    assert res["ok"] is True

    # Diniego.
    t2 = threading.Thread(target=lambda: res.__setitem__("d", s.approval_fn("x.write", {}, None)))
    t2.start()
    s.events.get(timeout=2)
    s.answer.put(False)
    t2.join(2)
    assert res["d"] is False


def test_web_secret_never_in_event():
    """L'evento del segreto contiene SOLO la label; il valore va solo al tool."""
    import threading
    from hemmy.interfaces import web

    s = web._WebSession()
    res = {}
    t = threading.Thread(target=lambda: res.__setitem__("v", s.secret_fn("conn per staX")))
    t.start()
    ev = s.events.get(timeout=2)
    assert ev["type"] == "secret_request"
    assert "value" not in ev and "SUPERSECRET" not in str(ev)
    assert "staX" in ev["label"]
    s.answer.put("SUPERSECRET")
    t.join(2)
    assert res["v"] == "SUPERSECRET"


def test_web_extract_mermaid_and_audit(tmp_path):
    from hemmy.interfaces import web

    md = "intro\n```mermaid\ngraph TD; A-->B\n```\nresto"
    assert web._extract_mermaid(md) == "graph TD; A-->B"
    assert web._extract_mermaid("nessun diagramma") == ""

    # Audit: file mancante -> lista vuota; con righe JSON -> più recenti per prime.
    assert web._read_audit(str(tmp_path / "missing.log")) == []
    p = tmp_path / "audit.log"
    p.write_text(
        '{"action":"a","outcome":"success"}\n{"action":"b","outcome":"error"}\n',
        encoding="utf-8",
    )
    rows = web._read_audit(str(p))
    assert [r["action"] for r in rows] == ["b", "a"]


# --------------------------------------------- GitHub branch handling (commit/branch)


def _fake_gh_http(state):
    """_http finto che instrada le chiamate GitHub e tiene traccia dei branch/calls."""
    def fake(method, url, headers, body=None):
        state["calls"].append((method, url))
        if method == "GET" and url.endswith("/repos/me/repo"):
            return 200, {"default_branch": "main"}
        if method == "GET" and "/branches" in url:
            return 200, [
                {"name": n, "protected": False, "commit": {"sha": s}}
                for n, s in state["branches"].items()
            ]
        if method == "GET" and "/git/ref/heads/" in url:
            br = url.split("/git/ref/heads/", 1)[1].split("?")[0]
            if br in state["branches"]:
                return 200, {"object": {"sha": state["branches"][br]}}
            raise RuntimeError("HTTP 404: Not Found")
        if method == "POST" and url.endswith("/git/refs"):
            br = body["ref"].split("refs/heads/", 1)[1]
            state["branches"][br] = body["sha"]
            return 201, {"ref": body["ref"]}
        if method == "GET" and "/git/commits/" in url:
            return 200, {"tree": {"sha": "tree-base"}}
        if method == "GET" and "/commits/" in url:
            return 200, {"sha": "sha-" + url.rsplit("/", 1)[1]}
        if method == "POST" and url.endswith("/git/trees"):
            return 201, {"sha": "tree-new"}
        if method == "POST" and url.endswith("/git/commits"):
            return 201, {"sha": "commit-new"}
        if method == "PATCH" and "/git/refs/heads/" in url:
            br = url.split("/git/refs/heads/", 1)[1]
            state["branches"][br] = body["sha"]
            return 200, {}
        if method == "PUT" and "/contents/" in url:
            return 201, {"content": {}}
        raise AssertionError(f"chiamata non gestita: {method} {url}")

    return fake


def _gh_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "T")
    monkeypatch.setenv("GITHUB_REPO", "me/repo")


def test_github_commit_creates_missing_branch(monkeypatch):
    """Branch inesistente su repo POPOLATO: viene creato dal default e si committa lì."""
    _gh_env(monkeypatch)
    state = {"branches": {"main": "sha-main"}, "calls": []}
    monkeypatch.setattr(github_tools, "_http", _fake_gh_http(state))
    res = github_tools.commit_files({"infra/x.tf": "resource {}"}, "msg", branch="feature/y")
    assert res["branch"] == "feature/y"
    assert res.get("commit_sha") == "commit-new"
    assert "feature/y" in state["branches"]  # branch creato, non scritto su main
    # È stato creato un ref per feature/y (POST /git/refs), non un bootstrap.
    assert any(m == "POST" and u.endswith("/git/refs") for m, u in state["calls"])


def test_github_commit_empty_repo_bootstraps(monkeypatch):
    """Repo VUOTO (nessun commit): bootstrap via Contents API, non creazione branch."""
    _gh_env(monkeypatch)
    state = {"branches": {}, "calls": []}  # nessun branch => repo vuoto
    monkeypatch.setattr(github_tools, "_http", _fake_gh_http(state))
    res = github_tools.commit_files({"README.md": "hi"}, "init", branch="main")
    assert res.get("bootstrap") is True
    assert "README.md" in res["committed"]
    assert any(m == "PUT" and "/contents/" in u for m, u in state["calls"])


def test_github_commit_missing_branch_no_autocreate(monkeypatch):
    """create_branch_if_missing=False: errore chiaro invece di scrivere altrove."""
    _gh_env(monkeypatch)
    state = {"branches": {"main": "sha-main"}, "calls": []}
    monkeypatch.setattr(github_tools, "_http", _fake_gh_http(state))
    with pytest.raises(RuntimeError, match="inesistente"):
        github_tools.commit_files(
            {"a.tf": "x"}, "m", branch="feature/z", create_branch_if_missing=False
        )


def test_github_create_and_list_branches(monkeypatch):
    _gh_env(monkeypatch)
    state = {"branches": {"main": "sha-main"}, "calls": []}
    monkeypatch.setattr(github_tools, "_http", _fake_gh_http(state))
    c = github_tools.create_branch("develop")
    assert c["created"] is True and state["branches"]["develop"] == "sha-main"
    # Idempotente: ricrearlo non è un errore.
    state["branches"]["develop"] = "sha-main"
    lst = github_tools.list_branches()
    assert lst["default_branch"] == "main"
    assert {b["name"] for b in lst["branches"]} == {"main", "develop"}


# ------------------------------------------------- GitHub PR / GitFlow tools


def test_github_create_pull_request(monkeypatch):
    _gh_env(monkeypatch)
    calls = []

    def fake(method, url, headers, body=None):
        calls.append((method, url, body))
        if method == "POST" and url.endswith("/pulls"):
            return 201, {"number": 7, "html_url": "http://pr/7", "state": "open"}
        raise AssertionError(f"non gestita: {method} {url}")

    monkeypatch.setattr(github_tools, "_http", fake)
    res = github_tools.create_pull_request("feature/x", "dev", "promo", body="b")
    assert res["created"] is True and res["number"] == 7
    assert res["head"] == "feature/x" and res["base"] == "dev"


def test_github_create_pull_request_existing(monkeypatch):
    _gh_env(monkeypatch)

    def fake(method, url, headers, body=None):
        if method == "POST" and url.endswith("/pulls"):
            raise RuntimeError("HTTP 422: A pull request already exists")
        if method == "GET" and "/pulls?state=open" in url:
            return 200, [{"number": 3, "html_url": "http://pr/3", "state": "open"}]
        raise AssertionError(f"non gestita: {method} {url}")

    monkeypatch.setattr(github_tools, "_http", fake)
    res = github_tools.create_pull_request("dev", "uat", "promo")
    assert res.get("already_exists") is True and res["number"] == 3


def test_github_create_pull_request_same_branch(monkeypatch):
    _gh_env(monkeypatch)
    monkeypatch.setattr(github_tools, "_http", lambda *a, **k: (200, {}))
    with pytest.raises(ValueError, match="coincidere"):
        github_tools.create_pull_request("dev", "dev", "x")


def test_github_merge_pull_request(monkeypatch):
    _gh_env(monkeypatch)

    def fake(method, url, headers, body=None):
        if method == "GET" and url.endswith("/pulls/7"):
            return 200, {"merged": False, "mergeable": True}
        if method == "PUT" and url.endswith("/pulls/7/merge"):
            return 200, {"merged": True, "sha": "abc"}
        raise AssertionError(f"non gestita: {method} {url}")

    monkeypatch.setattr(github_tools, "_http", fake)
    res = github_tools.merge_pull_request(7, method="squash")
    assert res["merged"] is True and res["sha"] == "abc"


def test_github_merge_pull_request_not_mergeable(monkeypatch):
    _gh_env(monkeypatch)

    def fake(method, url, headers, body=None):
        if method == "GET" and url.endswith("/pulls/9"):
            return 200, {"merged": False, "mergeable": False}
        raise AssertionError(f"non gestita: {method} {url}")

    monkeypatch.setattr(github_tools, "_http", fake)
    with pytest.raises(RuntimeError, match="non mergiabile"):
        github_tools.merge_pull_request(9)


def test_github_delete_branch_refuses_default(monkeypatch):
    _gh_env(monkeypatch)
    monkeypatch.setattr(github_tools, "_http", _fake_gh_http({"branches": {"main": "s"}, "calls": []}))
    with pytest.raises(ValueError, match="default"):
        github_tools.delete_branch("main")


def test_github_delete_branch(monkeypatch):
    _gh_env(monkeypatch)
    state = {"branches": {"main": "s"}, "calls": []}

    def fake(method, url, headers, body=None):
        state["calls"].append((method, url))
        if method == "GET" and url.endswith("/repos/me/repo"):
            return 200, {"default_branch": "main"}
        if method == "DELETE" and "/git/refs/heads/feature/x" in url:
            return 204, {}
        raise AssertionError(f"non gestita: {method} {url}")

    monkeypatch.setattr(github_tools, "_http", fake)
    res = github_tools.delete_branch("feature/x")
    assert res["deleted"] is True


# ----------------------------------------------------------------- Meta-tooling
from hemmy.tools.meta import meta_tools as _meta


def test_meta_analyze_needs_new_tool():
    docs = {"adf.list_pipelines": "Elenca le pipeline della Data Factory."}
    res = _meta.analyze_request(docs, "confronta un file draw.io con l'infra terraform")
    assert res["verdict"] in ("needs_new_tool", "combine")


def test_meta_analyze_covered():
    docs = {"adf.list_pipelines": "Elenca le pipeline della Data Factory ADF."}
    res = _meta.analyze_request(docs, "elenca le pipeline della data factory")
    assert res["verdict"] == "covered"
    assert res["candidates"][0]["name"] == "adf.list_pipelines"


def test_meta_propose_tool_valid():
    code = "def run(**kwargs):\n    return {'ok': True}\n"
    p = _meta.propose_tool("demo.echo", "Echo demo.", code)
    assert p["valid"] is True and not p["errors"]
    # `propose_tool` non ritorna più `preview`/`target_file` all'agente (information
    # disclosure verso il modello): l'anteprima integrale è mostrata solo all'utente
    # umano al momento dell'approvazione di `meta.install_tool`.
    assert "preview" not in p and "target_file" not in p
    assert p["name"] == "demo.echo"


def test_meta_propose_tool_invalid_name_and_code():
    p = _meta.propose_tool("BadName", "x", "def nope():\n    pass\n")
    assert p["valid"] is False
    assert any("nome" in e for e in p["errors"])
    assert any("run" in e for e in p["errors"])


def test_meta_propose_tool_blocks_dangerous_patterns():
    # os.system è ora un pattern BLOCCANTE (errors), non più un semplice warning:
    # la sola review umana si è dimostrata insufficiente a fermare tool pericolosi.
    code = "import os\ndef run(**kwargs):\n    os.system('echo hi')\n    return {}\n"
    p = _meta.propose_tool("demo.risky", "d", code)
    assert p["errors"]
    assert not p["valid"]


def test_meta_propose_tool_flags_risky_as_warning():
    # Le chiamate di rete restano un warning non bloccante (non toccano il filesystem
    # né il codice sorgente): l'utente può comunque approvarle consapevolmente.
    code = (
        "import requests\n"
        "def run(**kwargs):\n"
        "    requests.get('https://example.com')\n"
        "    return {}\n"
    )
    p = _meta.propose_tool("demo.network", "d", code)
    assert p["warnings"]


def test_meta_install_and_remove_roundtrip():
    name = "demoinstall.ping"
    code = "def run(**kwargs):\n    return {'pong': True}\n"
    try:
        r = _meta.install_tool(name, "Ping demo.", code)
        assert r["installed"] == name and r["reload_required"] is True
        names = [p["name"] for p in _meta.list_plugins()["installed"]]
        assert name in names
    finally:
        _meta.remove_plugin(name)
    names = [p["name"] for p in _meta.list_plugins()["installed"]]
    assert name not in names


def test_meta_install_rejects_bad_code():
    import pytest
    with pytest.raises(ValueError):
        _meta.install_tool("demo.bad", "d", "def notrun():\n    pass\n")


# ------------------------------------------------------------------
# Multi-utente: UserStore (auth, sessioni, settings, segreti) + plugin per-utente
# ------------------------------------------------------------------
def test_userstore_register_and_authenticate(tmp_path):
    from hemmy.auth.users import UserStore
    us = UserStore(str(tmp_path / "users.db"))
    u = us.register("alice", "supersegreta1", role="architect")
    assert u["username"] == "alice" and u["role"] == "architect"
    assert us.authenticate("alice", "supersegreta1")["id"] == u["id"]
    assert us.authenticate("alice", "sbagliata") is None
    assert us.authenticate("ALICE", "supersegreta1") is not None  # case-insensitive


def test_userstore_rejects_weak_and_duplicate(tmp_path):
    import pytest
    from hemmy.auth.users import UserStore
    us = UserStore(str(tmp_path / "users.db"))
    with pytest.raises(ValueError):
        us.register("bob", "short")           # password troppo corta
    with pytest.raises(ValueError):
        us.register("ab", "longenough123")    # username troppo corto
    us.register("bob", "longenough123")
    with pytest.raises(ValueError):
        us.register("bob", "anotherlong123")  # duplicato


def test_userstore_sessions(tmp_path):
    from hemmy.auth.users import UserStore
    us = UserStore(str(tmp_path / "users.db"))
    u = us.register("carla", "longenough123")
    tok = us.create_session(u["id"])
    assert us.resolve_session(tok)["username"] == "carla"
    us.revoke_session(tok)
    assert us.resolve_session(tok) is None
    assert us.resolve_session("inesistente") is None


def test_userstore_settings_rejects_secrets(tmp_path):
    import pytest
    from hemmy.auth.users import UserStore
    us = UserStore(str(tmp_path / "users.db"))
    u = us.register("dave", "longenough123")
    us.set_settings(u["id"], {"subscription_id": "sub-1", "model": "deepseek-chat"})
    assert us.get_settings(u["id"])["subscription_id"] == "sub-1"
    with pytest.raises(ValueError):
        us.set_settings(u["id"], {"deepseek_api_key": "sk-xxx"})  # segreto -> rifiutato


def test_userstore_secret_encrypted_at_rest(tmp_path):
    from hemmy.auth.users import UserStore
    db = str(tmp_path / "users.db")
    us = UserStore(db)
    u = us.register("emma", "longenough123")
    us.set_secret(u["id"], "deepseek_api_key", "sk-super-secret")
    assert us.get_secret(u["id"], "deepseek_api_key") == "sk-super-secret"
    assert us.list_secret_names(u["id"]) == ["deepseek_api_key"]
    # a riposo il valore NON è in chiaro nel DB
    raw = open(db, "rb").read()
    assert b"sk-super-secret" not in raw


def test_user_plugins_isolated(tmp_path, monkeypatch):
    from hemmy import plugins as plugmod
    d1 = plugmod.user_plugins_dir(1)
    d2 = plugmod.user_plugins_dir(2)
    assert d1 != d2 and d1.name == "1" and d2.name == "2"
    # nessun tool per-utente installato -> lista vuota, non errore
    assert plugmod.load_user_plugins(99999) == []


# ------------------------------------------------------------------
# Multi-utente: isolamento dei tool generati per-utente
# ------------------------------------------------------------------
def test_meta_install_per_user_dir(tmp_path, monkeypatch):
    from hemmy import plugins as plugmod
    from hemmy.tools.meta import meta_tools
    # L'area plugin runtime "reale" è ora fuori dal sorgente (data/plugins/); per
    # il test la spostiamo su una tmp_path, così il containment check di sicurezza
    # (`is_inside_runtime_plugins_area`) resta attivo e verificato, non aggirato.
    monkeypatch.setattr(plugmod, "_RUNTIME_PLUGINS_DIR", tmp_path)
    udir = tmp_path / "u42"
    code = "def run(**kwargs):\n    return {'ok': True}\n"
    r = meta_tools.install_tool("useriso.ping", "Ping isolato.", code, base_dir=str(udir))
    assert r["installed"] == "useriso.ping" and r["reload_required"] is True
    # il file vive nell'area dell'utente, non nel runtime condiviso
    assert (udir / "useriso__ping.py").exists()
    names = [p["name"] for p in meta_tools.list_plugins(base_dir=str(udir))["installed"]]
    assert "useriso.ping" in names
    # un'altra area utente NON vede il tool
    other = [p["name"] for p in meta_tools.list_plugins(base_dir=str(tmp_path / "u99"))["installed"]]
    assert "useriso.ping" not in other
    meta_tools.remove_plugin("useriso.ping", base_dir=str(udir))
    assert not (udir / "useriso__ping.py").exists()


def test_meta_install_rejects_base_dir_outside_runtime_area(tmp_path, monkeypatch):
    """Sicurezza: anche se un `base_dir` inatteso arrivasse a `install_tool`
    (bypassando lo strip fatto da `core.agent._RESERVED_ARG_NAMES`), il
    containment check deve rifiutare qualunque percorso fuori dall'area
    plugin runtime — in particolare qualunque path dentro il codice sorgente."""
    import pytest
    from hemmy import plugins as plugmod
    from hemmy.tools.meta import meta_tools

    monkeypatch.setattr(plugmod, "_RUNTIME_PLUGINS_DIR", tmp_path / "data" / "plugins")
    code = "def run(**kwargs):\n    return {'ok': True}\n"
    # Tentativo di scrivere fuori dall'area consentita (es. dentro il sorgente).
    outside = plugmod._PACKAGE_DIR.parent / "core"
    with pytest.raises(ValueError, match="non consentito"):
        meta_tools.install_tool("evil.tool", "d", code, base_dir=str(outside))


def test_meta_install_rejects_bad_code_per_user(tmp_path):
    import pytest
    from hemmy.tools.meta import meta_tools
    with pytest.raises(ValueError):
        meta_tools.install_tool("bad.tool", "d", "def nope():\n    pass\n", base_dir=str(tmp_path / "u1"))
