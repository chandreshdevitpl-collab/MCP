"""
db_tools.py — FastMCP tools for read-only database access.

All connection credentials are passed as tool arguments — no environment
variables are required. Only SELECT statements are permitted.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastmcp import FastMCP

log = logging.getLogger("mcp-server.db")

MAX_ROWS: int = 500

_ALLOWED = re.compile(
    r"^\s*(with\s[\s\S]+?\bselect\b|select\b)",
    re.IGNORECASE,
)
_BLOCKED_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|replace|"
    r"grant|revoke|exec|execute|call|pragma|attach|detach|vacuum|"
    r"load\s+data|into\s+outfile|into\s+dumpfile|"
    r"analyze|explain\s+analyze)\b",
    re.IGNORECASE,
)


def _validate_select(sql: str) -> None:
    clean = sql.strip()
    if not _ALLOWED.match(clean):
        raise ValueError(
            "Only SELECT queries are allowed. "
            f"Your statement starts with something else: {clean[:60]!r}"
        )
    blocked = _BLOCKED_KEYWORDS.search(clean)
    if blocked:
        raise ValueError(
            f"Forbidden keyword detected: '{blocked.group()}'. "
            "Only read-only SELECT statements are permitted."
        )


def _mysql_connect(
    host: str,
    database: str,
    user: str,
    password: str,
    port: int = 3306,
    charset: str = "utf8mb4",
    connect_timeout: int = 10,
    ssl: bool = False,
):
    try:
        import mysql.connector
        from mysql.connector import errorcode
    except ImportError:
        raise RuntimeError(
            "mysql-connector-python is not installed.\n"
            "Run:  pip install mysql-connector-python"
        )

    kwargs: dict[str, Any] = {
        "host":             host,
        "port":             port,
        "database":         database,
        "user":             user,
        "password":         password,
        "connect_timeout":  connect_timeout,
        "charset":          charset,
        "collation":        "utf8mb4_unicode_ci",
        "autocommit":       True,
        "consume_results":  True,
        "ssl_disabled":     not ssl,
    }

    try:
        log.info("Connecting to MySQL %s:%s db=%s user=%s ssl=%s", host, port, database, user, ssl)
        return mysql.connector.connect(**kwargs)
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            raise RuntimeError(f"MySQL access denied for user '{user}' on {host}:{port}.") from err
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            raise RuntimeError(f"MySQL database '{database}' does not exist on {host}:{port}.") from err
        elif "timed out" in str(err).lower() or "can't connect" in str(err).lower():
            raise RuntimeError(
                f"Cannot reach MySQL server at {host}:{port}. "
                f"Check host, port, and firewall. Original error: {err}"
            ) from err
        else:
            raise RuntimeError(f"MySQL connection error: {err}") from err


def _rows_to_dicts(cursor) -> list[dict[str, Any]]:
    if cursor.description is None:
        return []
    cols = [desc[0] for desc in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchmany(MAX_ROWS)]


def register_db_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def mysql_test_connection(
        host: str,
        database: str,
        user: str,
        password: str,
        port: int = 3306,
        charset: str = "utf8mb4",
        connect_timeout: int = 10,
        ssl: bool = False,
    ) -> dict:
        """
        Ping a MySQL server and return connection metadata.
        Use this to verify credentials before running queries.

        Args:
            host:            MySQL server hostname or IP.
            database:        Database / schema name.
            user:            MySQL username.
            password:        MySQL password.
            port:            TCP port (default: 3306).
            charset:         Character set (default: utf8mb4).
            connect_timeout: Seconds before timeout (default: 10).
            ssl:             Enable SSL/TLS (default: False).
        """
        try:
            conn = _mysql_connect(host, database, user, password, port, charset, connect_timeout, ssl)
            cur = conn.cursor()

            cur.execute("SELECT VERSION();")
            version = cur.fetchone()[0]

            cur.execute("SHOW STATUS LIKE 'Ssl_cipher';")
            ssl_row = cur.fetchone()
            ssl_cipher = ssl_row[1] if ssl_row else ""

            conn.close()
            return {
                "connected":      True,
                "server_version": version,
                "host":           host,
                "port":           port,
                "database":       database,
                "user":           user,
                "ssl_requested":  ssl,
                "ssl_cipher":     ssl_cipher,
                "ssl_active":     bool(ssl_cipher),
            }
        except Exception as exc:
            log.warning("Connection test failed: %s", exc)
            return {
                "connected": False,
                "host":      host,
                "port":      port,
                "database":  database,
                "user":      user,
                "error":     str(exc),
            }

    @mcp.tool()
    def list_tables(
        host: str,
        database: str,
        user: str,
        password: str,
        port: int = 3306,
        charset: str = "utf8mb4",
        connect_timeout: int = 10,
        ssl: bool = False,
    ) -> list[str]:
        """
        Return all table names in the given MySQL database.

        Args:
            host:            MySQL server hostname or IP.
            database:        Database / schema name.
            user:            MySQL username.
            password:        MySQL password.
            port:            TCP port (default: 3306).
            charset:         Character set (default: utf8mb4).
            connect_timeout: Seconds before timeout (default: 10).
            ssl:             Enable SSL/TLS (default: False).
        """
        conn = _mysql_connect(host, database, user, password, port, charset, connect_timeout, ssl)
        try:
            cur = conn.cursor()
            cur.execute("SHOW TABLES;")
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

    @mcp.tool()
    def describe_table(
        host: str,
        database: str,
        user: str,
        password: str,
        table_name: str,
        port: int = 3306,
        charset: str = "utf8mb4",
        connect_timeout: int = 10,
        ssl: bool = False,
    ) -> list[dict]:
        """
        Return the column schema for a specific table.

        Args:
            host:            MySQL server hostname or IP.
            database:        Database / schema name.
            user:            MySQL username.
            password:        MySQL password.
            table_name:      The table to describe.
            port:            TCP port (default: 3306).
            charset:         Character set (default: utf8mb4).
            connect_timeout: Seconds before timeout (default: 10).
            ssl:             Enable SSL/TLS (default: False).
        """
        conn = _mysql_connect(host, database, user, password, port, charset, connect_timeout, ssl)
        try:
            cur = conn.cursor()
            cur.execute(f"SHOW CREATE TABLE `{table_name}`;")
            return _rows_to_dicts(cur)
        finally:
            conn.close()

    @mcp.tool()
    def run_select_query(
        host: str,
        database: str,
        user: str,
        password: str,
        sql: str,
        port: int = 3306,
        charset: str = "utf8mb4",
        connect_timeout: int = 10,
        ssl: bool = False,
    ) -> dict:
        """
        Execute a read-only SELECT query and return results.

        Only SELECT statements are accepted. INSERT / UPDATE / DELETE / DROP
        and any mutation or admin commands are blocked.

        Args:
            host:            MySQL server hostname or IP.
            database:        Database / schema name.
            user:            MySQL username.
            password:        MySQL password.
            sql:             A valid SELECT SQL statement.
            port:            TCP port (default: 3306).
            charset:         Character set (default: utf8mb4).
            connect_timeout: Seconds before timeout (default: 10).
            ssl:             Enable SSL/TLS (default: False).

        Returns:
            dict with columns, rows, row_count, truncated flag.
        """
        _validate_select(sql)

        conn = _mysql_connect(host, database, user, password, port, charset, connect_timeout, ssl)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in (cur.description or [])]
            rows = _rows_to_dicts(cur)
            return {
                "columns":   columns,
                "rows":      rows,
                "row_count": len(rows),
                "truncated": len(rows) >= MAX_ROWS,
                "max_rows":  MAX_ROWS,
            }
        finally:
            conn.close()

    @mcp.tool()
    def preview_table(
        host: str,
        database: str,
        user: str,
        password: str,
        table_name: str,
        limit: int = 10,
        port: int = 3306,
        charset: str = "utf8mb4",
        connect_timeout: int = 10,
        ssl: bool = False,
    ) -> dict:
        """
        Return the first N rows of a table (safe preview, no raw SQL needed).

        Args:
            host:            MySQL server hostname or IP.
            database:        Database / schema name.
            user:            MySQL username.
            password:        MySQL password.
            table_name:      Name of the table to preview.
            limit:           Rows to return (default: 10, max: 100).
            port:            TCP port (default: 3306).
            charset:         Character set (default: utf8mb4).
            connect_timeout: Seconds before timeout (default: 10).
            ssl:             Enable SSL/TLS (default: False).
        """
        limit = min(max(1, limit), 100)
        sql = f"SELECT * FROM `{table_name}` LIMIT {limit};"
        _validate_select(sql)

        conn = _mysql_connect(host, database, user, password, port, charset, connect_timeout, ssl)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in (cur.description or [])]
            rows = _rows_to_dicts(cur)
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
        finally:
            conn.close()
