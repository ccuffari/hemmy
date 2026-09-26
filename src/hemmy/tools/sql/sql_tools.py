"""Tool per Azure SQL Database (SOLA LETTURA).

Funzioni per leggere lo schema delle tabelle di destinazione.
Usano una connessione pyodbc costruita in `infra/clients.py`.
Le SQL sono parametrizzate e in sola lettura; le query di scrittura/distruttive
sono comunque bloccate a livello di guardrail.
"""

from __future__ import annotations

from typing import Any


def list_tables(conn: Any, schema: str = "dbo") -> list[str]:
    """Elenca le tabelle di uno schema."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = ?
        ORDER BY TABLE_NAME
        """,
        schema,
    )
    return [row[0] for row in cursor.fetchall()]


def get_table_schema(conn: Any, table: str, schema: str = "dbo") -> list[dict[str, Any]]:
    """Restituisce le colonne di una tabella (nome, tipo, nullability, lunghezza)."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
        """,
        schema,
        table,
    )
    columns = []
    for name, data_type, is_nullable, max_len in cursor.fetchall():
        columns.append(
            {
                "name": name,
                "type": data_type,
                "nullable": is_nullable == "YES",
                "max_length": max_len,
            }
        )
    return columns


def get_row_count(conn: Any, table: str, schema: str = "dbo") -> int:
    """Restituisce il numero di righe di una tabella (COUNT(*)).

    Il nome tabella/schema viene validato contro le tabelle esistenti per evitare
    SQL injection (non è possibile parametrizzare gli identificatori).
    """
    valid_tables = set(list_tables(conn, schema))
    if table not in valid_tables:
        raise ValueError(f"Tabella '{schema}.{table}' inesistente o non accessibile.")

    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM [{schema}].[{table}]")
    return int(cursor.fetchone()[0])


# ===================================================================== WRITE
# ATTENZIONE: esegue SQL che MODIFICA il database (CREATE/INSERT/UPDATE...).
# Registrata come azione di scrittura: richiede approvazione umana (guardrail).


def execute_write(conn: Any, query: str) -> dict[str, Any]:
    """Esegue un comando SQL di scrittura (DDL/DML) e committa.

    Utile ad es. per creare la tabella di destinazione di una pipeline.
    Il guardrail chiede approvazione umana prima dell'esecuzione e può bloccare
    comandi distruttivi in base alla policy.
    """
    cursor = conn.cursor()
    cursor.execute(query)
    rows = cursor.rowcount
    conn.commit()
    return {"rows_affected": rows, "query": query}


# --------------------------------------------- Connessione a un DB ARBITRARIO
# La connection string ODBC viene richiesta all'operatore al momento
# (man-in-the-middle) tramite `secret_provider` e NON viene mai restituita,
# loggata o passata al modello LLM.


def _connect_with_secret(secret_provider: Any, resource_label: str) -> Any:
    """Apre una connessione pyodbc transitoria chiedendo la connection string.

    Il segreto resta locale: non viene restituito al chiamante.
    """
    import pyodbc

    connection_string = secret_provider(f"connection string ODBC per {resource_label}")
    if not connection_string:
        raise ValueError(
            "Nessuna connection string fornita dall'operatore: operazione annullata."
        )
    return pyodbc.connect(connection_string)


def list_tables_for_connection(
    secret_provider: Any, resource_label: str = "database esterno", schema: str = "dbo"
) -> list[str]:
    """Elenca le tabelle di un DB arbitrario (connection string chiesta all'operatore)."""
    conn = _connect_with_secret(secret_provider, resource_label)
    try:
        return list_tables(conn, schema)
    finally:
        conn.close()


def get_table_schema_for_connection(
    secret_provider: Any,
    table: str,
    resource_label: str = "database esterno",
    schema: str = "dbo",
) -> list[dict[str, Any]]:
    """Schema di una tabella in un DB arbitrario (connection string chiesta all'operatore)."""
    conn = _connect_with_secret(secret_provider, resource_label)
    try:
        return get_table_schema(conn, table, schema)
    finally:
        conn.close()


def get_row_count_for_connection(
    secret_provider: Any,
    table: str,
    resource_label: str = "database esterno",
    schema: str = "dbo",
) -> int:
    """Conteggio righe in un DB arbitrario (connection string chiesta all'operatore)."""
    conn = _connect_with_secret(secret_provider, resource_label)
    try:
        return get_row_count(conn, table, schema)
    finally:
        conn.close()


def execute_write_for_connection(
    secret_provider: Any, query: str, resource_label: str = "database esterno"
) -> dict[str, Any]:
    """Esegue SQL di scrittura su un DB arbitrario.

    Doppio gate: connection string chiesta all'operatore + approvazione umana
    (l'azione è classificata come scrittura nel guardrail).
    """
    conn = _connect_with_secret(secret_provider, resource_label)
    try:
        return execute_write(conn, query)
    finally:
        conn.close()


# =============================================================== MANAGEMENT-PLANE
# Gestione di SQL Server e database a livello di risorsa Azure (azure-mgmt-sql).
# Le credenziali admin (login + password) sono fornite dall'operatore, mai dal LLM.


def list_sql_servers(mgmt_client: Any) -> list[dict[str, Any]]:
    """Elenca i SQL Server della subscription."""
    servers = []
    for s in mgmt_client.servers.list():
        servers.append(
            {
                "name": s.name,
                "location": s.location,
                "fqdn": s.fully_qualified_domain_name,
            }
        )
    return servers


def list_sql_databases(
    mgmt_client: Any, resource_group: str, server_name: str
) -> list[dict[str, Any]]:
    """Elenca i database di un SQL Server."""
    dbs = []
    for d in mgmt_client.databases.list_by_server(resource_group, server_name):
        dbs.append({"name": d.name, "status": d.status, "sku": d.sku.name if d.sku else None})
    return dbs


def create_sql_server(
    mgmt_client: Any,
    secret_provider: Any,
    resource_group: str,
    server_name: str,
    location: str,
) -> dict[str, Any]:
    """[WRITE] Crea un SQL Server (management-plane).

    Login e password admin vengono chiesti all'operatore (mai passati al modello).
    """
    admin_login = secret_provider(
        f"admin LOGIN (username) per il SQL Server '{server_name}'"
    )
    admin_password = secret_provider(
        f"admin PASSWORD per il SQL Server '{server_name}'"
    )
    if not admin_login or not admin_password:
        raise ValueError(
            "Credenziali admin non fornite dall'operatore: operazione annullata."
        )

    from azure.mgmt.sql.models import Server

    poller = mgmt_client.servers.begin_create_or_update(
        resource_group,
        server_name,
        Server(
            location=location,
            administrator_login=admin_login,
            administrator_login_password=admin_password,
        ),
    )
    server = poller.result()
    return {
        "created_sql_server": server.name,
        "resource_group": resource_group,
        "fqdn": server.fully_qualified_domain_name,
    }


def create_sql_database(
    mgmt_client: Any,
    resource_group: str,
    server_name: str,
    database_name: str,
    location: str,
) -> dict[str, Any]:
    """[WRITE] Crea un database su un SQL Server esistente (management-plane)."""
    from azure.mgmt.sql.models import Database

    poller = mgmt_client.databases.begin_create_or_update(
        resource_group, server_name, database_name, Database(location=location)
    )
    db = poller.result()
    return {
        "created_sql_database": db.name,
        "server": server_name,
        "resource_group": resource_group,
    }
