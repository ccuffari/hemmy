# Plugin auto-generato per il tool 'sql.get_wait_stats'.
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
    top = int(kwargs.get('top', 15))
    query = f'''
    SELECT TOP {top}
        wait_type,
        waiting_tasks_count,
        wait_time_ms,
        max_wait_time_ms,
        signal_wait_time_ms,
        CAST(100.0 * wait_time_ms / NULLIF(SUM(wait_time_ms) OVER (), 0) AS DECIMAL(5,2)) AS pct
    FROM sys.dm_os_wait_stats
    WHERE wait_type NOT IN (
        'CLR_SEMAPHORE','LAZYWRITER_SLEEP','RESOURCE_QUEUE','SLEEP_TASK',
        'SLEEP_SYSTEMTASK','SQLTRACE_BUFFER_FLUSH','WAITFOR','LOGMGR_QUEUE',
        'CHECKPOINT_QUEUE','REQUEST_FOR_DEADLOCK_SEARCH','XE_TIMER_EVENT',
        'BROKER_TO_FLUSH','BROKER_TASK_STOP','CLR_MANUAL_EVENT','CLR_AUTO_EVENT',
        'DISPATCHER_QUEUE_SEMAPHORE','FT_IFTS_SCHEDULER_IDLE_WAIT','XE_DISPATCHER_WAIT',
        'XE_DISPATCHER_JOIN','SQLTRACE_INCREMENTAL_FLUSH_SLEEP','ONDEMAND_TASK_QUEUE',
        'BROKER_EVENTHANDLER','SLEEP_BPOOL_FLUSH','SQLTRACE_WAIT_ENTRIES',
        'DIRTY_PAGE_POLL','HADR_FILESTREAM_IOMGR_IOCOMPLETION','SP_SERVER_DIAGNOSTICS_SLEEP'
    )
    AND wait_time_ms > 0
    ORDER BY wait_time_ms DESC
    '''
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(query)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return {'ok': True, 'count': len(rows), 'rows': rows}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

MANIFEST = {
    "tools": [
        {"name": "sql.get_wait_stats", "doc": "[READ] Analizza i wait stats su Azure SQL (sys.dm_os_wait_stats) per individuare il collo di bottiglia (I/O, CPU, lock, network). Args: {\"top\": int (opz, default 15)}. Ritorna tipo di wait, tempo totale, percentuale.", "write": False, "entrypoint": "run"}
    ]
}
