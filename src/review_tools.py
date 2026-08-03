"""
review_tools.py — FastMCP tool for retrieving staged git diff for code review.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastmcp import FastMCP


def register_review_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def get_staged_diff(repo_root: str) -> str:
        """
        Return the staged git diff (changes added with `git add`) for code review.

        Runs `git diff --staged` on the given repository and returns the full diff
        output. If there are no staged changes, a descriptive message is returned
        instead so the caller (Claude, VS Code, etc.) can report it cleanly.

        Args:
            repo_root: Absolute path to the git repository root
                       (e.g. C:/Users/me/my-project or /home/user/my-project).
        """
        root = Path(repo_root).resolve()

        if not root.is_dir():
            return f"ERROR: Directory does not exist: {root}"

        git_dir = root / ".git"
        if not git_dir.exists():
            return f"ERROR: '{root}' is not a git repository (no .git directory found)."

        try:
            result = subprocess.run(
                ["git", "-C", str(root), "diff", "--staged"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return "ERROR: git executable not found. Ensure git is installed and in PATH."
        except subprocess.TimeoutExpired:
            return "ERROR: git diff timed out after 30 seconds."

        if result.returncode != 0:
            err = result.stderr.strip() or "git diff --staged failed with no output."
            return f"ERROR: {err}"

        diff_output = result.stdout

        if not diff_output.strip():
            return (
                "No staged changes found in the repository. "
                "Stage your changes with `git add <file>` before requesting a code review."
            )

        return diff_output
