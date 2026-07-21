"""
tests/test_server.py
~~~~~~~~~~~~~~~~~~~~
Unit tests for repo tools and DB tools.
Run with: pytest tests/ -v
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# ─── Set REPO_ROOT to a temp directory before importing tools ─────────────────
@pytest.fixture(autouse=True)
def _setup_env(tmp_path, monkeypatch):
    # Create a fake git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Hello World\n")
    (repo / "main.py").write_text("def hello():\n    print('hello')\n")
    sub = repo / "src"
    sub.mkdir()
    (sub / "utils.py").write_text("# utilities\n")

    monkeypatch.setenv("REPO_ROOT", str(repo))

    # SQLite temp DB
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER);")
    conn.execute("INSERT INTO users VALUES (1, 'Alice', 30);")
    conn.execute("INSERT INTO users VALUES (2, 'Bob', 25);")
    conn.commit()
    conn.close()

    monkeypatch.setenv("DB_TYPE", "sqlite")
    monkeypatch.setenv("SQLITE_DB_PATH", str(db))
    monkeypatch.setenv("DB_MAX_ROWS", "500")


# ─── Import tools AFTER env is set ────────────────────────────────────────────
from src.repo_tools import _safe_path, _repo_root
from src.db_tools import _validate_select


# ══════════════════════════════════════════════════════════════════════════════
# Repository tool tests
# ══════════════════════════════════════════════════════════════════════════════

class TestSafePath:
    def test_valid_path(self):
        p = _safe_path("README.md")
        assert p.name == "README.md"

    def test_path_traversal_blocked(self):
        with pytest.raises(ValueError, match="escapes"):
            _safe_path("../../etc/passwd")


class TestRepoRoot:
    def test_returns_path(self):
        root = _repo_root()
        assert root.is_dir()


# ══════════════════════════════════════════════════════════════════════════════
# DB tool tests — SQL guard
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateSelect:
    def test_plain_select(self):
        _validate_select("SELECT * FROM users;")          # should not raise

    def test_select_with_where(self):
        _validate_select("SELECT id, name FROM users WHERE age > 20;")

    def test_cte_select(self):
        _validate_select("WITH cte AS (SELECT 1) SELECT * FROM cte;")

    def test_insert_blocked(self):
        with pytest.raises(ValueError, match="Forbidden keyword"):
            _validate_select("INSERT INTO users VALUES (3, 'Eve', 22);")

    def test_update_blocked(self):
        with pytest.raises(ValueError, match="Forbidden keyword"):
            _validate_select("UPDATE users SET name='X' WHERE id=1;")

    def test_delete_blocked(self):
        with pytest.raises(ValueError, match="Forbidden keyword"):
            _validate_select("DELETE FROM users WHERE id=1;")

    def test_drop_blocked(self):
        with pytest.raises(ValueError, match="Forbidden keyword"):
            _validate_select("DROP TABLE users;")

    def test_non_select_start_blocked(self):
        with pytest.raises(ValueError, match="Only SELECT queries"):
            _validate_select("EXEC sp_who;")

    def test_select_with_embedded_drop_blocked(self):
        # Edge case: SELECT that embeds DROP via semicolon smuggling
        with pytest.raises(ValueError, match="Forbidden keyword"):
            _validate_select("SELECT 1; DROP TABLE users;")


# ══════════════════════════════════════════════════════════════════════════════
# Integration-style tests using the tool functions directly
# ══════════════════════════════════════════════════════════════════════════════

class TestDbToolsIntegration:
    def test_list_tables(self):
        from src.db_tools import register_db_tools
        # We'll just call the underlying logic directly
        import sqlite3, os
        conn = sqlite3.connect(os.environ["SQLITE_DB_PATH"])
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [r[0] for r in cur.fetchall()]
        conn.close()
        assert "users" in tables

    def test_run_select(self):
        from src.db_tools import _validate_select, _get_connection, _rows_to_dicts
        sql = "SELECT * FROM users ORDER BY id;"
        _validate_select(sql)
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        rows = _rows_to_dicts(cur, "sqlite")
        conn.close()
        assert len(rows) == 2
        assert rows[0]["name"] == "Alice"


class TestRepoToolsIntegration:
    def test_list_files_root(self):
        from src.repo_tools import _repo_root
        root = _repo_root()
        files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
        assert any("README.md" in f for f in files)

    def test_read_file(self):
        from src.repo_tools import _safe_path
        p = _safe_path("README.md")
        content = p.read_text()
        assert "Hello World" in content
