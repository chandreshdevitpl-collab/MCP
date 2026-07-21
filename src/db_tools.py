"""
db_tools.py — FastMCP tools for read-only database access.

Supports: MySQL (mysql-connector-python).
Only SELECT statements are permitted; any other SQL raises an error.
Connection strings are read from environment variables — never hard-coded.

MySQL remote server support:
  - Plain TCP connection (MYSQL_HOST / MYSQL_PORT)
  - SSL/TLS (MYSQL_SSL=true + optional MYSQL_SSL_CA / MYSQL_SSL_CERT / MYSQL_SSL_KEY)
  - SSH tunnel (handled externally; point MYSQL_HOST=127.0.0.1 + MYSQL_PORT=tunnel_port)
  - Connection timeout & retry configurable via MYSQL_CONNECT_TIMEOUT
"""

from __future__ import annotations

import os
import re
from typing import Any

from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Constants / config
# ---------------------------------------------------------------------------

# Maximum rows returned per query (safety limit)
MAX_ROWS: int = int(os.environ.get("DB_MAX_ROWS", "500"))

# Supported DB types
DB_TYPE_MYSQL = "mysql"


# ---------------------------------------------------------------------------
# SQL guard — block anything that isn't a plain SELECT
# ---------------------------------------------------------------------------

# Allow only SELECT (including CTEs: WITH … SELECT …)
_ALLOWED = re.compile(
    r"^\s*(with\s[\s\S]+?\bselect\b|select\b)",
    re.IGNORECASE,
)

# Explicit blocklist for mutation / admin keywords
_BLOCKED_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|replace|"
    r"grant|revoke|exec|execute|call|pragma|attach|detach|vacuum|"
    r"load\s+data|into\s+outfile|into\s+dumpfile|"
    r"analyze|explain\s+analyze)\b",
    re.IGNORECASE,
)


def _validate_select(sql: str) -> None:
    """
    Raise ValueError if *sql* is not a safe SELECT statement.
    Two-pass check: allow-list (must start with SELECT/WITH) + deny-list.
    """
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


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _get_db_type() -> str:
    return DB_TYPE_MYSQL




def _mysql_connection():
    """
    Build a mysql-connector-python connection for a remote MySQL server.

    Environment variables read:
      MYSQL_HOST             — hostname or IP of the MySQL server (required)
      MYSQL_PORT             — TCP port, default 3306
      MYSQL_DB               — database / schema name (required)
      MYSQL_USER             — MySQL username (required)
      MYSQL_PASSWORD         — MySQL password (required)
      MYSQL_CONNECT_TIMEOUT  — seconds before connection attempt fails (default 10)
      MYSQL_SSL              — "true" / "1" to enable SSL/TLS (default false)
      MYSQL_SSL_CA           — path to CA certificate file (PEM)
      MYSQL_SSL_CERT         — path to client certificate file (PEM)
      MYSQL_SSL_KEY          — path to client private key file (PEM)
      MYSQL_SSL_VERIFY_CERT  — "false" / "0" to skip cert verification (default true)
      MYSQL_CHARSET          — character set, default "utf8mb4"
    """
    try:
        import mysql.connector
        from mysql.connector import errorcode
    except ImportError:
        raise RuntimeError(
            "mysql-connector-python is not installed.\n"
            "Run:  pip install mysql-connector-python"
        )
    print("mysql-connector-python is available, proceeding with MySQL connection setup...", os.environ.get("MYSQL_HOST", "not connection."))
    host     = os.environ.get("MYSQL_HOST", "")
    port     = int(os.environ.get("MYSQL_PORT", "3306"))
    database = os.environ.get("MYSQL_DB", "")
    user     = os.environ.get("MYSQL_USER", "")
    password = os.environ.get("MYSQL_PASSWORD", "")
    timeout  = int(os.environ.get("MYSQL_CONNECT_TIMEOUT", "10"))
    charset  = os.environ.get("MYSQL_CHARSET", "utf8mb4")

    # ── Basic validation ────────────────────────────────────────────────
    missing = [k for k, v in [
        ("MYSQL_HOST", host), ("MYSQL_DB", database),
        ("MYSQL_USER", user), ("MYSQL_PASSWORD", password),
    ] if not v]
    if missing:
        raise RuntimeError(
            f"Missing required MySQL environment variable(s): {', '.join(missing)}\n"
            "Set them in your .env file or claude_desktop_config.json."
        )

    # ── Base connection kwargs ──────────────────────────────────────────
    kwargs: dict[str, Any] = {
        "host":             host,
        "port":             port,
        "database":         database,
        "user":             user,
        "password":         password,
        "connect_timeout":  timeout,
        "charset":          charset,
        "collation":        "utf8mb4_unicode_ci",
        "autocommit":       True,          # no implicit transactions
        "consume_results":  True,
        # Keep connection alive for the duration of one tool call
        "connection_timeout": timeout,
    }

    # ── SSL / TLS ───────────────────────────────────────────────────────
    use_ssl = os.environ.get("MYSQL_SSL", "false").lower() in ("true", "1", "yes")
    if use_ssl:
        ssl_ca   = os.environ.get("MYSQL_SSL_CA",   "")
        ssl_cert = os.environ.get("MYSQL_SSL_CERT", "")
        ssl_key  = os.environ.get("MYSQL_SSL_KEY",  "")
        verify   = os.environ.get("MYSQL_SSL_VERIFY_CERT", "true").lower() not in ("false", "0", "no")

        ssl_dict: dict[str, Any] = {"verify_cert": verify}
        if ssl_ca:
            ssl_dict["ca"]   = ssl_ca
        if ssl_cert:
            ssl_dict["cert"] = ssl_cert
        if ssl_key:
            ssl_dict["key"]  = ssl_key

        kwargs["ssl_disabled"] = False
        kwargs["ssl_ca"]       = ssl_ca   or None
        kwargs["ssl_cert"]     = ssl_cert or None
        kwargs["ssl_key"]      = ssl_key  or None
        kwargs["ssl_verify_cert"] = verify
        kwargs["ssl_verify_identity"] = verify
    else:
        kwargs["ssl_disabled"] = True

    # ── Connect with friendly error messages ───────────────────────────
    try:
        print(f"Attempting MySQL connection to {host}:{port} with user '{user}' and database '{database}' (SSL: {'enabled' if use_ssl else 'disabled'})...")
        conn = mysql.connector.connect(**kwargs)
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            raise RuntimeError(
                f"MySQL access denied for user '{user}' on {host}:{port}. "
                "Check MYSQL_USER and MYSQL_PASSWORD."
            ) from err
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            raise RuntimeError(
                f"MySQL database '{database}' does not exist on {host}:{port}."
            ) from err
        elif "timed out" in str(err).lower() or "can't connect" in str(err).lower():
            raise RuntimeError(
                f"Cannot reach MySQL server at {host}:{port}. "
                "Check MYSQL_HOST, MYSQL_PORT, firewall rules, and that the server is running.\n"
                f"Original error: {err}"
            ) from err
        else:
            raise RuntimeError(f"MySQL connection error: {err}") from err

    return conn


def _get_connection():
    return _mysql_connection()


# ---------------------------------------------------------------------------
# Row serialization
# ---------------------------------------------------------------------------

def _rows_to_dicts(cursor, db_type: str) -> list[dict[str, Any]]:
    """Convert cursor rows to plain dicts regardless of DB driver."""
    if cursor.description is None:
        return []
    cols = [desc[0] for desc in cursor.description]
    rows = []
    for row in cursor.fetchmany(MAX_ROWS):
        rows.append(dict(zip(cols, row)))
    return rows


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_db_tools(mcp: FastMCP) -> None:
    """Attach all database tools to the FastMCP instance."""

    # ------------------------------------------------------------------
    @mcp.tool()
    def db_info() -> dict:
        """
        Return connection metadata: DB type, host/path, and current database name.
        Passwords and secrets are never included.
        """
        db_type = _get_db_type()
        info: dict[str, Any] = {"db_type": db_type, "max_rows_per_query": MAX_ROWS}

        info["host"]            = os.environ.get("MYSQL_HOST", "")
        info["port"]            = os.environ.get("MYSQL_PORT", "3306")
        info["database"]        = os.environ.get("MYSQL_DB", "")
        info["user"]            = os.environ.get("MYSQL_USER", "")
        info["ssl_enabled"]     = os.environ.get("MYSQL_SSL", "false").lower() in ("true", "1", "yes")
        info["connect_timeout"] = os.environ.get("MYSQL_CONNECT_TIMEOUT", "10")
        info["charset"]         = os.environ.get("MYSQL_CHARSET", "utf8mb4")

        return info

    # ------------------------------------------------------------------
    @mcp.tool()
    def mysql_test_connection(host, port, db , user, ssl) -> dict:
        """
        Ping the remote MySQL server and return server metadata.
        Use this to verify your connection is working before running queries.

        Returns:
            dict with: connected (bool), server_version, host, port,
                       database, user, ssl_active, error (if any).
        """
        host     = os.environ.get("MYSQL_HOST", "")
        port     = os.environ.get("MYSQL_PORT", "3306")
        database = os.environ.get("MYSQL_DB", "")
        user     = os.environ.get("MYSQL_USER", "")
        ssl_on   = os.environ.get("MYSQL_SSL", "false").lower() in ("true", "1", "yes")

        try:
            conn = _mysql_connection()
            cur = conn.cursor()

            # Server version
            cur.execute("SELECT VERSION();")
            version = cur.fetchone()[0]

            # Check if SSL is actually active
            cur.execute("SHOW STATUS LIKE 'Ssl_cipher';")
            ssl_row   = cur.fetchone()
            ssl_cipher = ssl_row[1] if ssl_row else ""

            conn.close()
            return {
                "connected":      True,
                "server_version": version,
                "host":           host,
                "port":           port,
                "database":       database,
                "user":           user,
                "ssl_requested":  ssl_on,
                "ssl_cipher":     ssl_cipher,   # empty string = no SSL active
                "ssl_active":     bool(ssl_cipher),
            }
        except Exception as exc:
            print(f"Connection test failed: {exc}")
            return {
                "connected": False,
                "host":      host,
                "port":      port,
                "database":  database,
                "user":      user,
                "error":     str(exc),
            }

    # ------------------------------------------------------------------
    @mcp.tool()
    def list_tables() -> list[str]:
        """
        Return all table names available in the connected database.
        """
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SHOW TABLES;")
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    @mcp.tool()
    def describe_table(table_name: str) -> list[dict]:
        """
        Return the column schema for a specific table.

        Args:
            table_name: The name of the table to describe.

        Returns:
            List of dicts with column metadata (name, type, nullable, default, …).
        """
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(f"SHOW CREATE TABLE `{table_name}`;")  # noqa: S608
            return _rows_to_dicts(cur, DB_TYPE_MYSQL)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    @mcp.tool()
    def run_select_query(sql: str) -> dict:
        """
        Execute a read-only SELECT query and return the results.

        ⚠️  Only SELECT statements are accepted.
           INSERT / UPDATE / DELETE / DROP and any other mutation
           or admin commands are blocked and will raise an error.

        Args:
            sql: A valid SELECT SQL statement.

        Returns:
            dict with:
              - columns: list of column names
              - rows:    list of row dicts
              - row_count: number of rows returned
              - truncated: True if results were capped at MAX_ROWS
        """
        _validate_select(sql)          # <-- guard runs before any DB call

        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in (cur.description or [])]
            rows = _rows_to_dicts(cur, DB_TYPE_MYSQL)
            truncated = len(rows) >= MAX_ROWS

            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "max_rows": MAX_ROWS,
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    @mcp.tool()
    def preview_table(table_name: str, limit: int = 10) -> dict:
        """
        Return the first N rows of a table (safe preview, no raw SQL needed).

        Args:
            table_name: Name of the table.
            limit:      Number of rows to return (default 10, max 100).

        Returns:
            Same structure as run_select_query.
        """
        limit = min(max(1, limit), 100)
        sql = f"SELECT * FROM `{table_name}` LIMIT {limit};"    # noqa: S608

        _validate_select(sql)   # still validates even for built-in queries

        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in (cur.description or [])]
            rows = _rows_to_dicts(cur, DB_TYPE_MYSQL)
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
        finally:
            conn.close()
