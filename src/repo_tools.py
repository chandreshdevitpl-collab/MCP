"""
repo_tools.py — FastMCP tools for reading a local repository.

All paths are sandboxed inside the calling client's project root, resolved
the same way db_tools resolves it (see db_tools._project_root):
  - HTTP transport: the 'X-Project-Root' header on the request.
  - stdio transport: the REPO_ROOT environment variable.
There is deliberately no cwd-based fallback — see db_tools._project_root
for why.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

from .db_tools import _project_root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Return the sandboxed repository root (the client project root)."""
    return _project_root()


def _safe_path(relative: str) -> Path:
    """
    Resolve *relative* against REPO_ROOT and raise if it escapes the root.
    This prevents path-traversal attacks (e.g. ../../etc/passwd).
    """
    root = _repo_root()
    target = (root / relative).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(
            f"Path '{relative}' escapes the repository root. Access denied."
        )
    return target


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_repo_tools(mcp: FastMCP) -> None:
    """Attach all repository tools to the FastMCP instance."""

    # ------------------------------------------------------------------
    @mcp.tool()
    def repo_info() -> dict:
        """
        Return basic metadata about the configured repository:
        root path, current branch, last commit, and remote origin (if any).
        """
        root = _repo_root()

        def _git(*args: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True
            )
            return result.stdout.strip() if result.returncode == 0 else ""

        return {
            "root": str(root),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "last_commit": _git("log", "-1", "--pretty=%H %s"),
            "remote_origin": _git("remote", "get-url", "origin"),
            "status_summary": _git("status", "--short"),
        }

    # ------------------------------------------------------------------
    @mcp.tool()
    def list_files(
        directory: str = "",
        pattern: str = "*",
        recursive: bool = False,
    ) -> list[str]:
        """
        List files inside the repository.

        Args:
            directory: Sub-directory relative to REPO_ROOT (default: root).
            pattern:   Glob pattern, e.g. "*.py" or "*.md" (default: "*").
            recursive: Whether to search recursively (default: False).

        Returns:
            List of file paths relative to REPO_ROOT.
        """
        base = _safe_path(directory)
        if not base.is_dir():
            raise ValueError(f"'{directory}' is not a directory in the repository.")

        glob_fn = base.rglob if recursive else base.glob
        root = _repo_root()

        return sorted(
            str(p.relative_to(root))
            for p in glob_fn(pattern)
            if p.is_file()
        )

    # ------------------------------------------------------------------
    @mcp.tool()
    def read_file(path: str) -> str:
        """
        Return the text content of a file inside the repository.

        Args:
            path: File path relative to REPO_ROOT.

        Returns:
            Plain-text file content (UTF-8).
        """
        target = _safe_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"'{path}' not found in the repository.")

        # Refuse very large files (> 2 MB)
        size = target.stat().st_size
        if size > 2 * 1024 * 1024:
            raise ValueError(
                f"'{path}' is {size // 1024} KB — too large to read directly. "
                "Use list_files + read_file on a smaller slice."
            )

        return target.read_text(encoding="utf-8", errors="replace")

    # ------------------------------------------------------------------
    @mcp.tool()
    def search_in_repo(
        query: str,
        directory: str = "",
        file_pattern: str = "*",
        case_sensitive: bool = False,
        use_regex: bool = False,
    ) -> list[dict]:
        """
        Search for text across repository files.

        Args:
            query: Search text or regex pattern.
            directory: Subdirectory to search from.
            file_pattern: Glob pattern (default '*').
            case_sensitive: Whether search is case-sensitive.
            use_regex: Treat query as regex.

        Returns:
            List of matching locations.
        """
        import re
        from pathlib import Path

        base = _safe_path(directory)
        root = _repo_root()

        IGNORE_DIRS = {
            ".git",
            "node_modules",
            "dist",
            "build",
            "coverage",
            ".angular",
            ".next",
            ".nuxt",
            "vendor",
            "__pycache__",
            ".venv",
            "venv",
            ".idea",
            ".vscode",
        }

        ALLOWED_EXTENSIONS = {
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".html",
            ".css",
            ".scss",
            ".json",
            ".yaml",
            ".yml",
            ".xml",
            ".md",
            ".txt",
            ".sql",
            ".cs",
            ".java",
            ".go",
            ".php",
            ".rb",
            ".sh",
        }

        flags = 0 if case_sensitive else re.IGNORECASE

        if use_regex:
            pattern = re.compile(query, flags)
        else:
            pattern = re.compile(re.escape(query), flags)

        matches = []

        for filepath in base.rglob(file_pattern):

            if not filepath.is_file():
                continue

            # Skip ignored directories
            if any(part in IGNORE_DIRS for part in filepath.parts):
                continue

            # Skip unsupported extensions
            if filepath.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue

            # Skip very large files (>5MB)
            try:
                if filepath.stat().st_size > 5 * 1024 * 1024:
                    continue
            except Exception:
                continue

            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for line_no, line in enumerate(f, start=1):
                        if pattern.search(line):
                            matches.append(
                                {
                                    "file": str(filepath.relative_to(root)),
                                    "line_number": line_no,
                                    "line_content": line.rstrip(),
                                }
                            )
            except Exception:
                continue

        return matches

    # ------------------------------------------------------------------
    @mcp.tool()
    def git_log(max_entries: int = 10) -> list[dict]:
        """
        Return the recent Git commit history.

        Args:
            max_entries: How many commits to return (default: 10, max: 100).

        Returns:
            List of dicts with keys: hash, author, date, message.
        """
        max_entries = min(max(1, max_entries), 100)
        root = _repo_root()

        result = subprocess.run(
            [
                "git", "-C", str(root),
                "log", f"-{max_entries}",
                "--pretty=format:%H|||%an|||%ad|||%s",
                "--date=short",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git log failed: {result.stderr.strip()}")

        entries = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("|||", 3)
            if len(parts) == 4:
                entries.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })
        return entries

    # ------------------------------------------------------------------
    @mcp.tool()
    def file_tree(directory: str = "", max_depth: int = 3) -> str:
        """
        Return an ASCII file-tree of the repository (like the `tree` command).

        Args:
            directory: Sub-directory relative to REPO_ROOT (default: root).
            max_depth: Maximum depth to traverse (default: 3, max: 6).

        Returns:
            Multi-line string representing the directory tree.
        """
        max_depth = min(max(1, max_depth), 6)
        base = _safe_path(directory)
        root = _repo_root()

        lines: list[str] = [str(base.relative_to(root)) or "."]

        def _walk(path: Path, prefix: str, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                entries = sorted(
                    path.iterdir(),
                    key=lambda p: (p.is_file(), p.name.lower()),
                )
            except PermissionError:
                return

            # Skip hidden / common noise folders
            entries = [
                e for e in entries
                if not e.name.startswith(".")
                and e.name not in {"__pycache__", "node_modules", ".git"}
            ]

            for i, entry in enumerate(entries):
                connector = "└── " if i == len(entries) - 1 else "├── "
                lines.append(f"{prefix}{connector}{entry.name}")
                if entry.is_dir():
                    extension = "    " if i == len(entries) - 1 else "│   "
                    _walk(entry, prefix + extension, depth + 1)

        _walk(base, "", 1)
        return "\n".join(lines)
