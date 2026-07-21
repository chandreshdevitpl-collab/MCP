"""Simple git diff code review helper.

This tool reads the current git diff, loads best-practice rules from a
code review guidance file, and compares the diff against those rules.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_RULE_FILE = "codeReview.md"


@dataclass
class CodeReviewRule:
    description: str
    pattern: str | None


def load_rules(path: Path) -> list[CodeReviewRule]:
    if not path.exists():
        return []

    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    rules: list[CodeReviewRule] = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if "->" in line:
            description, pattern = [part.strip() for part in line.split("->", 1)]
            rules.append(CodeReviewRule(description=description, pattern=pattern or None))
        else:
            rules.append(CodeReviewRule(description=line, pattern=None))
    return rules


def get_git_diff(staged: bool = False, base: str | None = None) -> str:
    args = ["git", "diff", "--no-color"]
    if staged:
        args = ["git", "diff", "--cached", "--no-color"]
    if base:
        args += [base]

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z]{3,}", text)
    keywords = [token.lower() for token in tokens if len(token) > 3]
    return sorted(set(keywords), key=lambda x: (-len(x), x))


def match_rule(diff_text: str, rule: CodeReviewRule) -> tuple[bool, list[str]]:
    if rule.pattern:
        try:
            pattern = re.compile(rule.pattern, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(rule.pattern), re.IGNORECASE)
        matched = bool(pattern.search(diff_text))
        return matched, [m.group(0) for m in pattern.finditer(diff_text)][:5]

    keywords = [word for word in extract_keywords(rule.description) if len(word) > 3]
    matches = [kw for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", diff_text, re.IGNORECASE)]
    return bool(matches), matches[:5]


def run_agent_review(diff_text: str, rules: list[CodeReviewRule]) -> dict:
    review_items = []
    for rule in rules:
        matched, matches = match_rule(diff_text, rule)
        review_items.append(
            {
                "description": rule.description,
                "pattern": rule.pattern,
                "matched": matched,
                "matches": matches,
            }
        )

    return {
        "diff_summary": {
            "total_lines": len(diff_text.splitlines()),
            "changed_files": diff_text.count("diff --git "),
        },
        "rules": review_items,
    }


def format_review_output(review: dict, rules_path: Path) -> str:
    lines: list[str] = []
    lines.append("CODE REVIEW OUTPUT")
    lines.append(f"Rules file: {rules_path}")
    lines.append(f"Changed files: {review['diff_summary']['changed_files']}")
    lines.append(f"Diff lines: {review['diff_summary']['total_lines']}")
    lines.append("")

    if not review["rules"]:
        lines.append("No code review rules found. Create a codeReview.md file with rules.")
        return "\n".join(lines)

    for idx, item in enumerate(review["rules"], start=1):
        status = "MATCH" if item["matched"] else "NO MATCH"
        lines.append(f"{idx}. {item['description']}")
        if item["pattern"]:
            lines.append(f"   pattern: {item['pattern']}")
        lines.append(f"   status: {status}")
        if item["matches"]:
            lines.append(f"   examples: {', '.join(item['matches'])}")
        lines.append("")

    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare git diff against code review guidance and print a review summary."
    )
    parser.add_argument(
        "--rules",
        default=DEFAULT_RULE_FILE,
        help="Path to the code review guidance file (default: codeReview.md).",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Review staged changes instead of the working tree diff.",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Compare diff against a git base ref (for example HEAD).",
    )
    args = parser.parse_args(argv)

    rules_path = Path(args.rules)
    diff_text = get_git_diff(staged=args.staged, base=args.base)
    rules = load_rules(rules_path)
    review = run_agent_review(diff_text, rules)
    print(format_review_output(review, rules_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
