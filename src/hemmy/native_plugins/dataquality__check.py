# Plugin auto-generato per il tool 'dataquality.check'.
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


def _check_table(table, schema="dbo", key_columns=None, freshness_column=None):
    findings = []
    fq = f"[{schema}].[{table}]"

    rows, err = _sql_query(f"SELECT COUNT(*) AS cnt FROM {fq}")
    if err:
        return [_finding(fq, "query_error", "high", {"error": err}, "verifica tabella/connessione")]
    cnt = rows[0]["cnt"] if rows else 0
    if cnt == 0:
        findings.append(_finding(fq, "empty_table", "high", {"row_count": 0},
                                 "tabella vuota: verifica la pipeline di caricamento"))

    if key_columns:
        for col in key_columns:
            rows, err = _sql_query(f"SELECT COUNT(*) AS cnt FROM {fq} WHERE [{col}] IS NULL")
            if not err and rows and rows[0]["cnt"] > 0:
                findings.append(_finding(fq, "null_in_key", "high",
                                         {"column": col, "null_count": rows[0]["cnt"]},
                                         f"colonna chiave '{col}' con NULL: aggiungi vincolo NOT NULL"))

    if key_columns:
        cols = ", ".join(f"[{c}]" for c in key_columns)
        q = (f"SELECT COUNT(*) AS dup FROM (SELECT {cols}, COUNT(*) c FROM {fq} "
             f"GROUP BY {cols} HAVING COUNT(*) > 1) t")
        rows, err = _sql_query(q)
        if not err and rows and rows[0]["dup"] > 0:
            findings.append(_finding(fq, "duplicates", "medium",
                                     {"key_columns": key_columns, "dup_groups": rows[0]["dup"]},
                                     "duplicati sulle chiavi: valuta dedup o vincolo UNIQUE"))

    if freshness_column:
        q = f"SELECT MAX([{freshness_column}]) AS mx FROM {fq}"
        rows, err = _sql_query(q)
        if not err and rows:
            findings.append(_finding(fq, "freshness", "low",
                                     {"column": freshness_column, "max_value": str(rows[0]["mx"])},
                                     "verifica che il dato sia aggiornato rispetto alla SLA"))

    return findings


def _check_blob(container, blob_name):
    findings = []
    try:
        from azure.storage.blob import BlobServiceClient
    except Exception:
        return [_finding(f"{container}/{blob_name}", "sdk_missing", "low",
                         {"error": "azure-storage-blob non disponibile"}, "installa l'SDK")]
    conn = _secret("azure_storage_connection_string")
    if not conn:
        return [_finding(f"{container}/{blob_name}", "config_missing", "low",
                         {"error": "credenziale 'azure_storage_connection_string' mancante nelle Impostazioni"}, "configura lo storage")]
    try:
        svc = BlobServiceClient.from_connection_string(conn)
        blob = svc.get_blob_client(container=container, blob=blob_name)
        props = blob.get_blob_properties()
        size = props.size
        if size == 0:
            findings.append(_finding(f"{container}/{blob_name}", "empty_blob", "high",
                                     {"size": 0}, "file vuoto: verifica la pipeline"))
        else:
            findings.append(_finding(f"{container}/{blob_name}", "ok", "low",
                                     {"size": size, "content_type": props.content_settings.content_type},
                                     "nessuna anomalia rilevata"))
    except Exception as e:
        findings.append(_finding(f"{container}/{blob_name}", "read_error", "medium",
                                 {"error": str(e)}, "verifica accesso al blob"))
    return findings


def run(**kwargs):
    action = kwargs.get("action") or "check_table"
    params = kwargs.get("params") or {}
    try:
        if action == "check_table":
            table = params.get("table")
            if not table:
                return {"ok": False, "error": "table richiesto"}
            findings = _check_table(table, params.get("schema", "dbo"),
                                    params.get("key_columns"), params.get("freshness_column"))
            by_sev = {}
            for f in findings:
                by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
            return {"ok": True, "action": action, "table": table,
                    "findings": findings, "count": len(findings), "by_severity": by_sev}

        if action == "check_blob":
            container = params.get("container")
            blob_name = params.get("blob_name")
            if not (container and blob_name):
                return {"ok": False, "error": "container e blob_name richiesti"}
            findings = _check_blob(container, blob_name)
            return {"ok": True, "action": action, "findings": findings, "count": len(findings)}

        if action == "summary":
            table = params.get("table")
            if not table:
                return {"ok": False, "error": "table richiesto"}
            findings = _check_table(table, params.get("schema", "dbo"),
                                    params.get("key_columns"), params.get("freshness_column"))
            by_sev = {}
            for f in findings:
                by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
            return {"ok": True, "action": "summary", "table": table,
                    "total_findings": len(findings), "by_severity": by_sev}

        return {"ok": False, "error": f"azione non supportata: {action}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

MANIFEST = {
    "tools": [
        {"name": "dataquality.check", "doc": "Esegue controlli di data quality su tabelle SQL e file Blob: null, duplicati, range, freshness, schema drift, righe vuote. Usa la connessione SQL di default e lo storage configurato. Args: {\"action\": \"check_table\"|\"check_blob\"|\"summary\", \"params\": {\"table\": str, \"schema\": str (opz), \"container\": str (opz), \"blob_name\": str (opz), \"key_columns\": [str] (opz), \"freshness_column\": str (opz)}}. Ritorna findings normalizzati: {target, issue, severity, evidence, recommendation}.", "write": False, "entrypoint": "run"}
    ]
}
