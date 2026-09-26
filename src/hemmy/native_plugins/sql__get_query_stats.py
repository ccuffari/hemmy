# Plugin auto-generato per il tool 'sql.get_query_stats'.
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


def run(**kwargs):
    top = int(kwargs.get('top', 20))
    order_by = kwargs.get('order_by', 'cpu')
    order_map = {
        'cpu': 'total_worker_time DESC',
        'duration': 'total_elapsed_time DESC',
        'reads': 'total_logical_reads DESC',
        'writes': 'total_logical_writes DESC',
    }
    order_clause = order_map.get(order_by, 'total_worker_time DESC')
    query = f'''
    SELECT TOP {top}
        qs.execution_count,
        qs.total_worker_time/1000 AS total_cpu_ms,
        qs.total_elapsed_time/1000 AS total_duration_ms,
        qs.total_logical_reads,
        qs.total_logical_writes,
        qs.total_worker_time/qs.execution_count/1000 AS avg_cpu_ms,
        SUBSTRING(st.text, (qs.statement_start_offset/2)+1,
            ((CASE qs.statement_end_offset WHEN -1 THEN DATALENGTH(st.text)
              ELSE qs.statement_end_offset END - qs.statement_start_offset)/2)+1) AS query_text
    FROM sys.dm_exec_query_stats qs
    CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
    ORDER BY {order_clause}
    '''
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(query)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return {'ok': True, 'count': len(rows), 'order_by': order_by, 'rows': rows}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "sql.get_query_stats", "doc": "[READ] Analizza le query più lente/costose su Azure SQL via DMV (sys.dm_exec_query_stats + sys.dm_exec_sql_text). Args: {\"top\": int (opz, default 20), \"order_by\": \"cpu\"|\"duration\"|\"reads\"|\"writes\" (opz, default 'cpu')}. Ritorna query, tempo CPU, durata, letture/scritture, esecuzioni.", "write": False, "entrypoint": "run"}
    ]
}
