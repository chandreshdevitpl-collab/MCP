"""
env_utils.py — shared .env parsing and per-project config resolution.

This module intentionally has no knowledge of the MCP server's own .env.
It only knows how to read *.env files that live inside a given project
root, so that db_tools can load database credentials from the project
that spawned this server instance instead of the server's own config.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("mcp-server.env")


class ProjectDbConfigError(RuntimeError):
    """Raised when a target project's database configuration can't be resolved."""


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a single .env-style file into a plain dict. No side effects on os.environ."""
    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if ((value.startswith('"') and value.endswith('"')) or
                    (value.startswith("'") and value.endswith("'"))):
                value = value[1:-1]
            values[key] = value
    return values


def env_file_precedence(project_root: Path) -> list[Path]:
    """
    Return candidate .env files for *project_root*, lowest to highest precedence
    (later files override earlier ones on key conflicts) — mirrors the common
    .env / .env.<environment> / .env.local / .env.<environment>.local convention.
    """
    env_name = (
        os.environ.get("APP_ENV")
        or os.environ.get("NODE_ENV")
        or os.environ.get("ENVIRONMENT")
        or "development"
    )
    names = [".env", f".env.{env_name}", ".env.local", f".env.{env_name}.local"]
    return [project_root / name for name in names]


def load_project_env(project_root: Path) -> tuple[dict[str, str], list[Path]]:
    """
    Load and merge every existing .env candidate under *project_root*.

    Returns (merged_values, files_actually_loaded). Raises ProjectDbConfigError
    if the project root has no .env file at all.
    """
    candidates = env_file_precedence(project_root)
    loaded = [p for p in candidates if p.exists()]
    if not loaded:
        raise ProjectDbConfigError(
            f"No .env file found in target project root '{project_root}'. "
            f"Expected one of: {', '.join(p.name for p in candidates)}."
        )

    merged: dict[str, str] = {}
    for path in loaded:
        merged.update(parse_env_file(path))
    return merged, loaded


# Canonical config key -> accepted .env variable names, in priority order.
# The original single-name variable stays first priority for backward
# compatibility; additional aliases are checked only if higher-priority
# names are absent.
_DB_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "host": ("DB_HOST", "MYSQL_HOST", "MySQL_HOST"),
    "port": ("DB_PORT", "MYSQL_PORT", "MySQL_PORT"),
    "database": ("DB_NAME", "MYSQL_DB", "DATABASE_NAME", "MYSQL_DATABASE", "DB_DATABASE", "DATABASE"),
    "user": ("DB_USER", "MYSQL_USER", "DB_USERNAME", "MYSQL_USERNAME", "MySQL_User"),
    "password": (
        "DB_PASSWORD", "MYSQL_PASSWORD", "DB_PASS", "DATABASE_PASSWORD",
        "DATABASE_PASS", "MYSQL_PASS", "MySQL_PASSWORD",
    ),
    "connect_timeout": ("DB_CONNECT_TIMEOUT", "MYSQL_CONNECT_TIMEOUT"),
    "charset": ("DB_CHARSET", "MYSQL_CHARSET"),
    "ssl": ("DB_SSL", "MYSQL_SSL"),
    "ssl_ca": ("DB_SSL_CA", "MYSQL_SSL_CA"),
    "ssl_cert": ("DB_SSL_CERT", "MYSQL_SSL_CERT"),
    "ssl_key": ("DB_SSL_KEY", "MYSQL_SSL_KEY"),
    "ssl_verify_cert": ("DB_SSL_VERIFY_CERT", "MYSQL_SSL_VERIFY_CERT"),
}

_REQUIRED_KEYS = ("host", "database", "user", "password")
_REQUIRED_ENV_NAMES = {"host": "DB_HOST", "database": "DB_NAME", "user": "DB_USER", "password": "DB_PASSWORD"}


def _resolve_first_present(raw: dict[str, str], canonical: str, aliases: tuple[str, ...]) -> str | None:
    """
    Return the first non-empty value found in *raw* for the given aliases,
    checked in priority order (first alias wins). Logs which env var key
    was used to resolve *canonical* — never the value itself.
    """
    for alias in aliases:
        value = raw.get(alias)
        if value:
            log.debug("Resolved '%s' from environment variable '%s'.", canonical, alias)
            return value
    return None


def resolve_db_config(project_root: Path) -> tuple[dict[str, str], list[Path]]:
    """
    Load *project_root*'s own .env file(s) and extract database connection
    settings. Accepts the generic DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME
    names as first priority, falling back to the aliases in _DB_KEY_ALIASES
    (legacy MYSQL_* names and other common naming conventions) if the
    primary name is absent.

    Never reads from the MCP server's own environment/.env — only from files
    found inside project_root.
    """
    raw, loaded = load_project_env(project_root)

    config: dict[str, str] = {}
    for canonical, aliases in _DB_KEY_ALIASES.items():
        value = _resolve_first_present(raw, canonical, aliases)
        if value is not None:
            config[canonical] = value

    missing = [_REQUIRED_ENV_NAMES[k] for k in _REQUIRED_KEYS if not config.get(k)]
    if missing:
        raise ProjectDbConfigError(
            f"Target project '{project_root}' is missing required database "
            f"variable(s): {', '.join(missing)}. "
            f"Checked: {', '.join(str(p) for p in loaded)}."
        )

    config.setdefault("port", "3306")
    config.setdefault("connect_timeout", "10")
    config.setdefault("charset", "utf8mb4")
    config.setdefault("ssl", "false")
    config.setdefault("ssl_verify_cert", "true")
    return config, loaded
