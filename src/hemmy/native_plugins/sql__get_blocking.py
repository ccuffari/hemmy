# Plugin auto-generato per il tool 'sql.get_blocking'.
# Installato via meta.install_tool con approvazione umana.

import os
import struct


def _get_token_bytes():
    # Choke-point condiviso (sessione OAuth dell'utente corrente prima
    # di tutto, DefaultAzureCredential solo per CLI locale): PRIMA questa
    # funzione chiamava DefaultAzureCredential direttamente, condivisa
    # tra tutti gli utenti del processo.
    try:
        from hemmy.utils.azure_auth import get_sql_token
        tok = get_sql_token()
        tb = tok.encode('utf-16-le')
        return struct.pack('=I', len(tb)) + tb
    except Exception:
        return None


def _current_user_bound():
    try:
        from hemmy.auth.user_context import get_current_user_id
        return get_current_user_id() is not None
    except Exception:
        return False


def _sql_secret(name):
    # Segreto/contesto per-utente (Impostazioni), fallback a env var solo
    # per CLI locale senza portale (vedi auth.user_context).
    try:
        from hemmy.auth.user_context import get_current_user_secret_or_env
        return get_current_user_secret_or_env(name)
    except Exception:
        return os.environ.get(name.upper())


def _connect():
    import pyodbc
    # SQL_CONNECTION_STRING puo' incorporare credenziali SQL (UID/PWD): mai
    # come fallback per un utente autenticato (userebbe la STESSA stringa,
    # cifrata o meno, per tutti gli utenti del processo). Consentito SOLO in
    # CLI locale senza portale, per compatibilita' con l'uso storico.
    if not _current_user_bound():
        conn_str = os.environ.get('SQL_CONNECTION_STRING')
        if conn_str:
            return pyodbc.connect(conn_str, timeout=30)
    server = _sql_secret('sql_server')
    database = _sql_secret('sql_database')
    if not server or not database:
        raise RuntimeError("credenziali 'sql_server'/'sql_database' non configurate nelle Impostazioni (oppure SQL_CONNECTION_STRING/SQL_SERVER+SQL_DATABASE in CLI locale)")
    token_bytes = _get_token_bytes()
    if not token_bytes:
        raise RuntimeError('impossibile ottenere token Azure AD')
    cs = (f'Driver={{ODBC Driver 18 for SQL Server}};Server=tcp:{server},1433;'
          f'Database={database};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;')
    return pyodbc.connect(cs, attrs_before={1256: token_bytes}, timeout=30)


QUERY = '''
SELECT
    r.session_id,
    r.blocking_session_id,
    r.wait_type,
    r.wait_time,
    r.wait_resource,
    r.status,
    r.command,
    r.cpu_time,
    r.total_elapsed_time,
    s.login_name,
    s.host_name,
    s.program_name,
    SUBSTRING(t.text, (r.statement_start_offset/2)+1,
        ((CASE r.statement_end_offset WHEN -1 THEN DATALENGTH(t.text)
          ELSE r.statement_end_offset END - r.statement_start_offset)/2)+1) AS query_text
FROM sys.dm_exec_requests r
JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
WHERE r.blocking_session_id <> 0 OR r.session_id IN (
    SELECT DISTINCT blocking_session_id FROM sys.dm_exec_requests WHERE blocking_session_id <> 0
)
ORDER BY r.blocking_session_id, r.session_id
'''


def run(**kwargs):
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(QUERY)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return {'ok': True, 'count': len(rows), 'blocking': rows}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "sql.get_blocking", "doc": "[READ] Rileva sessioni bloccate e blocking chain su Azure SQL (sys.dm_exec_requests + sys.dm_exec_sessions). Args: {}. Ritorna sessioni bloccanti, bloccate, query e tempo di attesa.", "write": False, "entrypoint": "run"}
    ]
}
