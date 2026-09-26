# Plugin auto-generato per il tool 'sql.get_index_usage'.
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


MISSING_Q = '''
SELECT TOP {top}
    migs.avg_total_user_cost * migs.avg_user_impact * (migs.user_seeks + migs.user_scans) AS improvement_measure,
    mid.statement AS table_name,
    mid.equality_columns,
    mid.inequality_columns,
    mid.included_columns,
    migs.user_seeks,
    migs.user_scans,
    migs.avg_total_user_cost,
    migs.avg_user_impact
FROM sys.dm_db_missing_index_group_stats migs
JOIN sys.dm_db_missing_index_groups mig ON migs.group_handle = mig.index_group_handle
JOIN sys.dm_db_missing_index_details mid ON mig.index_handle = mid.index_handle
ORDER BY improvement_measure DESC
'''

UNUSED_Q = '''
SELECT TOP {top}
    OBJECT_NAME(i.object_id) AS table_name,
    i.name AS index_name,
    i.type_desc,
    us.user_seeks,
    us.user_scans,
    us.user_lookups,
    us.user_updates,
    us.last_user_seek,
    us.last_user_scan
FROM sys.indexes i
JOIN sys.dm_db_index_usage_stats us
    ON i.object_id = us.object_id AND i.index_id = us.index_id
WHERE us.database_id = DB_ID()
    AND i.type_desc <> 'HEAP'
    AND i.is_primary_key = 0
    AND i.is_unique_constraint = 0
    AND us.user_seeks = 0
    AND us.user_scans = 0
    AND us.user_lookups = 0
    AND us.user_updates > 0
ORDER BY us.user_updates DESC
'''


def _fetch(cur, q):
    cur.execute(q)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def run(**kwargs):
    mode = kwargs.get('mode', 'both')
    top = int(kwargs.get('top', 20))
    try:
        conn = _connect()
        cur = conn.cursor()
        result = {'ok': True, 'mode': mode}
        if mode in ('missing', 'both'):
            result['missing_indexes'] = _fetch(cur, MISSING_Q.format(top=top))
        if mode in ('unused', 'both'):
            result['unused_indexes'] = _fetch(cur, UNUSED_Q.format(top=top))
        conn.close()
        return result
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "sql.get_index_usage", "doc": "[READ] Analizza l'uso degli indici su Azure SQL: indici mancanti (sys.dm_db_missing_index_details) e indici inutilizzati (sys.dm_db_index_usage_stats). Args: {\"mode\": \"missing\"|\"unused\"|\"both\" (opz, default 'both'), \"top\": int (opz, default 20)}. Ritorna raccomandazioni indici.", "write": False, "entrypoint": "run"}
    ]
}
