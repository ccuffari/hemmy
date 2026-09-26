# Plugin auto-generato per il tool 'mdm.manage'.
# Installato via meta.install_tool con approvazione umana.

import os
import json
import difflib


def _secret(name):
    # Segreto per-utente (Impostazioni), fallback a env var solo per CLI
    # locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env

        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


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


def _similarity(a, b):
    if a is None or b is None:
        return 0.0
    return difflib.SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()


def _find_duplicates(table, schema, key_columns, threshold=0.85, limit=500):
    cols = ", ".join(f"[{c}]" for c in key_columns)
    q = f"SELECT TOP {limit} {cols} FROM [{schema}].[{table}]"
    rows, err = _sql_query(q)
    if err:
        return None, err
    dups = []
    n = len(rows)
    for i in range(n):
        for j in range(i + 1, n):
            scores = [_similarity(rows[i].get(c), rows[j].get(c)) for c in key_columns]
            avg = sum(scores) / len(scores) if scores else 0
            if avg >= threshold:
                dups.append({"record_a": rows[i], "record_b": rows[j],
                             "similarity": round(avg, 3)})
    return dups, None


def run(**kwargs):
    action = kwargs.get("action") or "summary"
    params = kwargs.get("params") or {}
    try:
        table = params.get("table")
        schema = params.get("schema", "dbo")
        key_columns = params.get("key_columns") or []
        threshold = float(params.get("threshold", 0.85))

        if action == "find_duplicates":
            if not (table and key_columns):
                return {"ok": False, "error": "table e key_columns richiesti"}
            dups, err = _find_duplicates(table, schema, key_columns, threshold)
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "action": action, "table": table,
                    "duplicates": dups, "count": len(dups),
                    "threshold": threshold}

        if action == "golden_record":
            if not (table and key_columns):
                return {"ok": False, "error": "table e key_columns richiesti"}
            dups, err = _find_duplicates(table, schema, key_columns, threshold)
            if err:
                return {"ok": False, "error": err}
            golden = []
            seen = set()
            for d in dups:
                key = tuple(str(d["record_a"].get(c)) for c in key_columns)
                if key not in seen:
                    seen.add(key)
                    golden.append({"golden": d["record_a"], "merged_from": [d["record_b"]]})
            return {"ok": True, "action": action, "table": table,
                    "golden_records": golden, "count": len(golden)}

        if action == "summary":
            if not (table and key_columns):
                return {"ok": False, "error": "table e key_columns richiesti"}
            dups, err = _find_duplicates(table, schema, key_columns, threshold)
            if err:
                return {"ok": False, "error": err}
            return {"ok": True, "action": "summary", "table": table,
                    "duplicate_pairs": len(dups), "threshold": threshold,
                    "note": "usa find_duplicates per i dettagli, golden_record per il merge"}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "mdm.manage", "doc": "Gestione Master Data Management (MDM): deduplica, golden record, matching tra record simili su tabelle SQL. Usa fuzzy matching (similarita' stringhe) e regole di survivorship. Args: {\"action\": \"find_duplicates\"|\"golden_record\"|\"summary\", \"params\": {\"table\": str, \"schema\": str (opz), \"key_columns\": [str], \"threshold\": float (opz, default 0.85)}}. Ritorna: {duplicates: [...], golden_records: [...], stats}.", "write": False, "entrypoint": "run"}
    ]
}
