# Plugin auto-generato per il tool 'dataquality.contracts'.
# Installato via meta.install_tool con approvazione umana.

import os
import json


def _secret(name):
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


def _finding(target, issue, severity, evidence, rec):
    return {"target": target, "issue": issue, "severity": severity,
            "evidence": evidence, "recommendation": rec}


def _sql_query(query):
    try:
        import pyodbc
    except Exception:
        return None, "pyodbc non disponibile"
    server = _secret("sql_server")
    db = _secret("sql_database")
    user = _secret("sql_user")
    pwd = _secret("sql_password")
    if not (server and db and user and pwd):
        return None, "credenziali 'sql_*' mancanti nelle Impostazioni"
    conn_str = (f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server};DATABASE={db};"
                f"UID={user};PWD={pwd};Encrypt=yes;TrustServerCertificate=no;")
    try:
        with pyodbc.connect(conn_str, timeout=30) as conn:
            cur = conn.cursor()
            cur.execute(query)
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return rows, None
    except Exception as e:
        return None, str(e)


def _get_table_schema(table, schema="dbo"):
    q = (f"SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH "
         f"FROM INFORMATION_SCHEMA.COLUMNS "
         f"WHERE TABLE_SCHEMA='{schema}' AND TABLE_NAME='{table}' "
         f"ORDER BY ORDINAL_POSITION")
    rows, err = _sql_query(q)
    if err:
        return None, err
    cols = []
    for r in rows:
        cols.append({"name": r["COLUMN_NAME"], "type": r["DATA_TYPE"],
                     "nullable": r["IS_NULLABLE"] == "YES",
                     "max_length": r.get("CHARACTER_MAXIMUM_LENGTH")})
    return cols, None


def _diff_schema(expected, actual):
    drift = []
    exp_names = {c["name"] for c in expected}
    act_names = {c["name"] for c in actual}
    for name in exp_names - act_names:
        drift.append({"column": name, "issue": "missing_column",
                      "severity": "high", "detail": "colonna attesa mancante"})
    for name in act_names - exp_names:
        drift.append({"column": name, "issue": "unexpected_column",
                      "severity": "medium", "detail": "colonna non prevista dal contract"})
    exp_map = {c["name"]: c for c in expected}
    act_map = {c["name"]: c for c in actual}
    for name in exp_names & act_names:
        if exp_map[name]["type"] != act_map[name]["type"]:
            drift.append({"column": name, "issue": "type_mismatch", "severity": "high",
                          "detail": f"atteso {exp_map[name]['type']}, reale {act_map[name]['type']}"})
    return drift


def run(**kwargs):
    action = kwargs.get("action") or "check"
    params = kwargs.get("params") or {}
    try:
        if action == "define":
            table = params.get("table")
            schema = params.get("schema", "dbo")
            if not table:
                return {"ok": False, "error": "table richiesto"}
            cols, err = _get_table_schema(table, schema)
            if err:
                return {"ok": False, "error": err}
            contract = {"table": table, "schema": schema, "version": 1,
                        "columns": cols}
            return {"ok": True, "action": "define", "contract": contract,
                    "note": "salva il contract per il check di drift"}

        if action == "check":
            table = params.get("table")
            schema = params.get("schema", "dbo")
            contract = params.get("contract")
            if not (table and contract):
                return {"ok": False, "error": "table e contract richiesti"}
            actual, err = _get_table_schema(table, schema)
            if err:
                return {"ok": False, "error": err}
            drift = _diff_schema(contract.get("columns", []), actual)
            return {"ok": True, "action": "check", "table": table,
                    "drift": drift, "count": len(drift),
                    "status": "OK" if not drift else "DRIFT_DETECTED"}

        if action == "diff":
            table = params.get("table")
            schema = params.get("schema", "dbo")
            expected = params.get("contract", {}).get("columns", [])
            actual, err = _get_table_schema(table, schema)
            if err:
                return {"ok": False, "error": err}
            drift = _diff_schema(expected, actual)
            return {"ok": True, "action": "diff", "table": table,
                    "drift": drift, "count": len(drift)}

        if action == "list":
            return {"ok": True, "action": "list", "contracts": [],
                    "note": "i contract sono passati come parametro; nessuno store persistente"}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "dataquality.contracts", "doc": "Gestisce data contracts e schema evolution: definisce lo schema atteso di una tabella/file, verifica drift rispetto allo schema reale, e traccia le versioni. Usa SQL (schema tabelle) e Blob (schema file). Args: {\"action\": \"define\"|\"check\"|\"list\"|\"diff\", \"params\": {\"table\": str (opz), \"schema\": str (opz), \"contract\": {} (opz), \"container\": str (opz), \"blob_name\": str (opz)}}. Ritorna: {contract, drift: [...], status}.", "write": False, "entrypoint": "run"}
    ]
}
