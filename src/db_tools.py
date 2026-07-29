"""
db_tools.py — FastMCP tools for read-only database access.

Supports: MySQL (mysql-connector-python).
Only SELECT statements are permitted; any other SQL raises an error.

Database credentials are NEVER read from this MCP server's own .env.
Every call resolves the *target project* first, then loads that project's
own .env to build a fresh connection:

  - HTTP transport (shared server, many projects): the calling client sends
    an `X-Project-Root` header per request, pointing at that project's root.
  - stdio transport (one server instance per project): falls back to
    REPO_ROOT / cwd, same convention repo_tools already uses.

A new connection is opened per call and always closed in a finally block;
nothing is cached or reused across projects.

MySQL remote server support:
  - Plain TCP connection (DB_HOST / DB_PORT)
  - SSL/TLS (DB_SSL=true + optional DB_SSL_CA / DB_SSL_CERT / DB_SSL_KEY)
  - SSH tunnel (handled externally; point DB_HOST=127.0.0.1 + DB_PORT=tunnel_port)
  - Connection timeout & retry configurable via DB_CONNECT_TIMEOUT
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from .env_utils import ProjectDbConfigError, resolve_db_config

log = logging.getLogger("mcp-server.db")

# The MCP server's own installation root — used only as a guard to make sure
# a resolved "client project root" never accidentally lands here.
_SERVER_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Constants / config
# ---------------------------------------------------------------------------

# Maximum rows returned per query (safety limit) — a server-level guardrail,
# not a credential, so it's fine to read from the MCP server's own env.
MAX_ROWS: int = int(os.environ.get("DB_MAX_ROWS", "500"))

# Supported DB types
DB_TYPE_MYSQL = "mysql"

_PROJECT_ROOT_HEADER = "x-project-root"

# --- TEMPORARY DEBUG LOGGING ------------------------------------------------
# Header name fragments that must never be logged in full (bearer tokens,
# cookies, API keys, ...). Safe to delete this block along with
# _log_incoming_request_headers() once X-Project-Root propagation is confirmed.
_SENSITIVE_HEADER_SUBSTRINGS = ("authorization", "cookie", "token", "secret", "api-key", "apikey")


def _mask_authorization(value: str) -> str:
    """Keep only the auth scheme (e.g. 'Bearer'); mask the credential itself."""
    if not value:
        return value
    scheme, _, _rest = value.partition(" ")
    return f"{scheme} ***" if _rest else "***"


def _mask_header_value(name: str, value: str) -> str:
    lname = name.lower()
    if lname == "authorization":
        return _mask_authorization(value)
    if any(s in lname for s in _SENSITIVE_HEADER_SUBSTRINGS):
        return "<masked>" if value else value
    return value


def _log_incoming_request_headers() -> None:
    """
    TEMPORARY DEBUG LOGGING — dumps every incoming HTTP header before
    _project_root() resolves the calling client's project. Does not affect
    project-root resolution; it fetches headers independently purely for
    logging. Remove this function (and its call site) once X-Project-Root
    propagation from the client is confirmed working end-to-end.
    """
    all_headers = get_http_headers(include_all=True)

    if not all_headers:
        log.info("[HEADER-DEBUG] No active HTTP request (stdio transport, or no headers present).")
        return

    masked = {name: _mask_header_value(name, value) for name, value in all_headers.items()}
    log.info("[HEADER-DEBUG] Incoming HTTP headers: %s", masked)

    project_root_value = all_headers.get(_PROJECT_ROOT_HEADER, "").strip()
    if project_root_value:
        normalized = Path(project_root_value).resolve()
        log.info("[HEADER-DEBUG] X-Project-Root raw value: %r", project_root_value)
        log.info("[HEADER-DEBUG] X-Project-Root normalized path: %s", normalized)
        log.info("[HEADER-DEBUG] X-Project-Root directory exists: %s", normalized.exists())
        log.info("[HEADER-DEBUG] X-Project-Root .env exists: %s", (normalized / ".env").exists())
    else:
        log.info("X-Project-Root header not present in request.")

    log.info("[HEADER-DEBUG] User-Agent: %s", all_headers.get("user-agent", "<not present>"))
    log.info("[HEADER-DEBUG] Host: %s", all_headers.get("host", "<not present>"))
    log.info("[HEADER-DEBUG] Content-Type: %s", all_headers.get("content-type", "<not present>"))
    log.info("[HEADER-DEBUG] Authorization: %s", masked.get("authorization", "<not present>"))
# --- END TEMPORARY DEBUG LOGGING --------------------------------------------


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
# Target-project resolution
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """
    Resolve the *calling client's* project root — the only place database
    credentials are ever loaded from. Checked per-call, never cached, so
    concurrent requests for different projects never bleed into each other.

    Requires an explicit signal from the client:
      - HTTP: the 'X-Project-Root' header on the request.
      - stdio: the REPO_ROOT variable the client set when it launched this
        server process (one process per project — see README).

    There is deliberately no cwd-based default: falling back to the current
    working directory would silently resolve to the MCP server's own
    installation root whenever a client omits the signal, which would load
    the server's own .env instead of the client's. That must never happen —
    so an undetermined project fails loudly instead.
    """
    _log_incoming_request_headers()  # TEMPORARY DEBUG LOGGING — see above

    headers = get_http_headers()
    header_root = headers.get(_PROJECT_ROOT_HEADER, "").strip()
    explicit_repo_root = os.environ.get("REPO_ROOT", "").strip()

    if header_root:
        root = Path(header_root).resolve()
    elif explicit_repo_root:
        root = Path(explicit_repo_root).resolve()
    else:
        raise ProjectDbConfigError(
            "Could not determine the calling client's project root — no "
            "'X-Project-Root' header was sent and no REPO_ROOT was set. "
            "Refusing to fall back to the MCP server's own configuration. "
            "Set the 'X-Project-Root' header (HTTP) or REPO_ROOT in the "
            "client's launch config (stdio) to point at the client project."
        )

    if root == _SERVER_ROOT:
        raise ProjectDbConfigError(
            f"Resolved client project root '{root}' is the MCP server's own "
            "installation directory. Refusing to use the server's own .env "
            "for database credentials — check the X-Project-Root header / "
            "REPO_ROOT value sent by the client."
        )
    return root


def _resolve_db_config() -> tuple[dict[str, str], Path, list[Path]]:
    """Resolve (config, project_root, env_files_loaded) for the current call."""
    root = _project_root()
    config, loaded = resolve_db_config(root)
    return config, root, loaded


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _get_db_type() -> str:
    return DB_TYPE_MYSQL


def _build_mysql_kwargs(config: dict[str, str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host":             config["host"],
        "port":             int(config.get("port", "3306")),
        "database":         config["database"],
        "user":             config["user"],
        "password":         config["password"],
        "connect_timeout":  int(config.get("connect_timeout", "10")),
        "charset":          config.get("charset", "utf8mb4"),
        "collation":        "utf8mb4_unicode_ci",
        "autocommit":       True,          # no implicit transactions
        "consume_results":  True,
    }

    use_ssl = config.get("ssl", "false").lower() in ("true", "1", "yes")
    if use_ssl:
        verify = config.get("ssl_verify_cert", "true").lower() not in ("false", "0", "no")
        kwargs["ssl_disabled"]        = False
        kwargs["ssl_ca"]              = config.get("ssl_ca") or None
        kwargs["ssl_cert"]            = config.get("ssl_cert") or None
        kwargs["ssl_key"]             = config.get("ssl_key") or None
        kwargs["ssl_verify_cert"]     = verify
        kwargs["ssl_verify_identity"] = verify
    else:
        kwargs["ssl_disabled"] = True

    return kwargs


def _mysql_connect(config: dict[str, str]):
    """
    Open a mysql-connector-python connection using an already-resolved
    per-project *config* dict (see env_utils.resolve_db_config). Never reads
    os.environ directly — connection details always come from the target
    project's own .env.
    """
    try:
        import mysql.connector
        from mysql.connector import errorcode
    except ImportError:
        raise RuntimeError(
            "mysql-connector-python is not installed.\n"
            "Run:  pip install mysql-connector-python"
        )

    kwargs = _build_mysql_kwargs(config)
    host, port = kwargs["host"], kwargs["port"]
    database, user = kwargs["database"], kwargs["user"]

    try:
        conn = mysql.connector.connect(**kwargs)
    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
            raise RuntimeError(
                f"MySQL access denied for user '{user}' on {host}:{port}. "
                "Check DB_USER/DB_PASSWORD (or MYSQL_USER/MYSQL_PASSWORD) "
                "in the target project's .env."
            ) from err
        elif err.errno == errorcode.ER_BAD_DB_ERROR:
            raise RuntimeError(
                f"MySQL database '{database}' does not exist on {host}:{port}."
            ) from err
        elif "timed out" in str(err).lower() or "can't connect" in str(err).lower():
            raise RuntimeError(
                f"Cannot reach MySQL server at {host}:{port}. "
                "Check DB_HOST/DB_PORT, firewall rules, and that the server is running.\n"
                f"Original error: {err}"
            ) from err
        else:
            raise RuntimeError(f"MySQL connection error: {err}") from err

    return conn


def _get_connection():
    """Zero-arg entry point kept for direct/test use — resolves the target
    project's config fresh and opens a connection. Caller is responsible for
    closing it (tools should prefer the _connect() context manager below)."""
    config, _root, _loaded = _resolve_db_config()
    return _mysql_connect(config)


@contextmanager
def _connect(config: dict[str, str], root: Path, loaded: list[Path]):
    """Open a connection for one request, logging each lifecycle step, and
    guarantee it's closed — even on error — via the finally block."""
    log.info("Target project: %s", root)
    log.info("Env file(s) loaded: %s", ", ".join(str(p) for p in loaded))
    log.info(
        "Database: %s (host=%s:%s, user=%s)",
        config["database"], config["host"], config.get("port", "3306"), config["user"],
    )
    log.info("Connection started")
    conn = _mysql_connect(config)
    log.info("Authentication successful")
    try:
        yield conn
    finally:
        conn.close()
        log.info("Connection closed")


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
        Return connection metadata for the *target project's* database: DB
        type, host, database name, and which project/.env it was resolved
        from. Passwords and secrets are never included.
        """
        try:
            config, root, loaded = _resolve_db_config()
        except ProjectDbConfigError as exc:
            log.warning("db_info: could not resolve target project config: %s", exc)
            return {"configured": False, "error": str(exc)}

        return {
            "configured":         True,
            "db_type":             DB_TYPE_MYSQL,
            "max_rows_per_query":  MAX_ROWS,
            "project_root":        str(root),
            "env_files_loaded":    [str(p) for p in loaded],
            "host":                config["host"],
            "port":                config.get("port", "3306"),
            "database":            config["database"],
            "user":                config["user"],
            "ssl_enabled":         config.get("ssl", "false").lower() in ("true", "1", "yes"),
            "connect_timeout":     config.get("connect_timeout", "10"),
            "charset":             config.get("charset", "utf8mb4"),
        }

    # ------------------------------------------------------------------
    @mcp.tool()
    def mysql_test_connection(host, port, db, user, ssl) -> dict:
        """
        Ping the target project's MySQL server and return server metadata.
        Use this to verify your connection is working before running queries.

        Note: host/port/db/user/ssl are accepted for tool-schema stability
        but ignored — connection details always come from the target
        project's own .env, never from caller-supplied values.

        Returns:
            dict with: connected (bool), server_version, host, port,
                       database, user, ssl_active, error (if any).
        """
        try:
            config, root, loaded = _resolve_db_config()
        except ProjectDbConfigError as exc:
            return {"connected": False, "error": str(exc)}

        try:
            with _connect(config, root, loaded) as conn:
                cur = conn.cursor()

                cur.execute("SELECT VERSION();")
                version = cur.fetchone()[0]

                cur.execute("SHOW STATUS LIKE 'Ssl_cipher';")
                ssl_row = cur.fetchone()
                ssl_cipher = ssl_row[1] if ssl_row else ""

            return {
                "connected":      True,
                "server_version": version,
                "project_root":   str(root),
                "host":           config["host"],
                "port":           config.get("port", "3306"),
                "database":       config["database"],
                "user":           config["user"],
                "ssl_requested":  config.get("ssl", "false").lower() in ("true", "1", "yes"),
                "ssl_cipher":     ssl_cipher,   # empty string = no SSL active
                "ssl_active":     bool(ssl_cipher),
            }
        except Exception as exc:
            log.error("Connection test failed: %s", exc)
            return {
                "connected":    False,
                "project_root": str(root),
                "host":         config.get("host", ""),
                "port":         config.get("port", "3306"),
                "database":     config.get("database", ""),
                "user":         config.get("user", ""),
                "error":        str(exc),
            }

    # ------------------------------------------------------------------
    @mcp.tool()
    def list_tables() -> list[str]:
        """
        Return all table names available in the target project's database.
        """
        config, root, loaded = _resolve_db_config()
        with _connect(config, root, loaded) as conn:
            cur = conn.cursor()
            cur.execute("SHOW TABLES;")
            return [row[0] for row in cur.fetchall()]

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
        config, root, loaded = _resolve_db_config()
        with _connect(config, root, loaded) as conn:
            cur = conn.cursor()
            cur.execute(f"SHOW CREATE TABLE `{table_name}`;")  # noqa: S608
            return _rows_to_dicts(cur, DB_TYPE_MYSQL)

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

        config, root, loaded = _resolve_db_config()
        log.info("Query execution started: %s", sql[:200])
        with _connect(config, root, loaded) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in (cur.description or [])]
            rows = _rows_to_dicts(cur, DB_TYPE_MYSQL)
            truncated = len(rows) >= MAX_ROWS
        log.info("Query completed — %d row(s)", len(rows))

        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "max_rows": MAX_ROWS,
        }

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

        config, root, loaded = _resolve_db_config()
        with _connect(config, root, loaded) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [desc[0] for desc in (cur.description or [])]
            rows = _rows_to_dicts(cur, DB_TYPE_MYSQL)
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
