"""
repo_tools.py — FastMCP tools for reading a local repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastmcp import FastMCP


def _safe_path(repo_root: str, relative: str) -> Path:
    root = Path(repo_root).resolve()
    if not relative:
        return root
    target = (root / relative).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path '{relative}' escapes the repository root. Access denied.")
    return target


def register_repo_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def repo_info(repo_root: str) -> dict:
        """
        Return basic metadata about a repository: branch, last commit, remote origin.

        Args:
            repo_root: Absolute path to the git repository root (e.g. C:/Users/me/my-project).
        """
        root = Path(repo_root).resolve()

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

    @mcp.tool()
    def list_files(
        repo_root: str,
        directory: str = "",
        pattern: str = "*",
        recursive: bool = False,
    ) -> list[str]:
        """
        List files inside a repository.

        Args:
            repo_root: Absolute path to the repository root.
            directory: Sub-directory relative to repo_root (default: root).
            pattern:   Glob pattern, e.g. "*.py" or "*.ts" (default: "*").
            recursive: Whether to search recursively (default: False).
        """
        root = Path(repo_root).resolve()
        base = _safe_path(repo_root, directory)
        if not base.is_dir():
            raise ValueError(f"'{directory}' is not a directory in the repository.")

        glob_fn = base.rglob if recursive else base.glob
        return sorted(
            str(p.relative_to(root))
            for p in glob_fn(pattern)
            if p.is_file()
        )

    @mcp.tool()
    def read_file(repo_root: str, path: str) -> str:
        """
        Return the text content of a file inside the repository.

        Args:
            repo_root: Absolute path to the repository root.
            path:      File path relative to repo_root.
        """
        target = _safe_path(repo_root, path)
        if not target.is_file():
            raise FileNotFoundError(f"'{path}' not found in the repository.")

        size = target.stat().st_size
        if size > 2 * 1024 * 1024:
            raise ValueError(
                f"'{path}' is {size // 1024} KB — too large to read directly. "
                "Use list_files + read_file on a smaller slice."
            )
        return target.read_text(encoding="utf-8", errors="replace")

    @mcp.tool()
    def search_in_repo(
        repo_root: str,
        query: str,
        directory: str = "",
        file_pattern: str = "*",
        case_sensitive: bool = False,
        use_regex: bool = False,
    ) -> list[dict]:
        """
        Search for text across repository files.

        Args:
            repo_root:      Absolute path to the repository root.
            query:          Search text or regex pattern.
            directory:      Subdirectory to search from (relative to repo_root).
            file_pattern:   Glob pattern (default '*').
            case_sensitive: Whether search is case-sensitive.
            use_regex:      Treat query as regex.
        """
        import re

        root = Path(repo_root).resolve()
        base = _safe_path(repo_root, directory)

        IGNORE_DIRS = {
            ".git", "node_modules", "dist", "build", "coverage",
            ".angular", ".next", ".nuxt", "vendor", "__pycache__",
            ".venv", "venv", ".idea", ".vscode",
        }
        ALLOWED_EXTENSIONS = {
            ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss",
            ".json", ".yaml", ".yml", ".xml", ".md", ".txt", ".sql",
            ".cs", ".java", ".go", ".php", ".rb", ".sh",
        }

        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(query if use_regex else re.escape(query), flags)

        matches = []
        for filepath in base.rglob(file_pattern):
            if not filepath.is_file():
                continue
            if any(part in IGNORE_DIRS for part in filepath.parts):
                continue
            if filepath.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            try:
                if filepath.stat().st_size > 5 * 1024 * 1024:
                    continue
            except Exception:
                continue
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for line_no, line in enumerate(f, start=1):
                        if pattern.search(line):
                            matches.append({
                                "file": str(filepath.relative_to(root)),
                                "line_number": line_no,
                                "line_content": line.rstrip(),
                            })
            except Exception:
                continue
        return matches

    @mcp.tool()
    def git_log_search(
        repo_root: str,
        max_entries: int = 10,
    ) -> list[dict]:
        """
        Return recent git commits from a repository.

        Args:
            repo_root:   Absolute path to the git repository root.
            max_entries: Number of commits to return (default: 10).
        """
        root = Path(repo_root).resolve()

        if not root.is_dir():
            return [{"error": f"Directory does not exist: {root}"}]

        try:
            result = subprocess.run(
                [
                    "git", "-C", str(root), "log", f"-{max_entries}",
                    "--pretty=format:%H|||%an|||%ad|||%s", "--date=short",
                ],
                capture_output=True, text=True, check=True, timeout=15,
            )
        except FileNotFoundError:
            return [{"error": "git executable not found. Ensure git is installed and in PATH."}]
        except subprocess.TimeoutExpired:
            return [{"error": "git log timed out after 15 seconds"}]
        except subprocess.CalledProcessError as e:
            err = e.stderr.strip() or e.stdout.strip() or "git log failed with no output"
            return [{"error": err}]

        if not result.stdout.strip():
            return [{"info": f"No commits found in: {root}"}]

        commits = []
        for line in result.stdout.splitlines():
            parts = line.split("|||", 3)
            if len(parts) != 4:
                continue
            commits.append({
                "hash": parts[0],
                "author": parts[1],
                "date": parts[2],
                "message": parts[3],
            })
        return commits

    @mcp.tool()
    def file_tree(repo_root: str, directory: str = "", max_depth: int = 3) -> str:
        """
        Return an ASCII file-tree of the repository (like the `tree` command).

        Args:
            repo_root:  Absolute path to the repository root.
            directory:  Sub-directory relative to repo_root (default: root).
            max_depth:  Maximum depth to traverse (default: 3, max: 6).
        """
        max_depth = min(max(1, max_depth), 6)
        root = Path(repo_root).resolve()
        base = _safe_path(repo_root, directory)

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
