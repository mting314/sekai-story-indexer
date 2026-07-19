#!/usr/bin/env python3
"""Conventional Commits (https://www.conventionalcommits.org) commit-msg check.

Local, dependency-free equivalent of ll-predictions' commitlint
(@commitlint/config-conventional) hook — the allowed types are kept in sync with
that repo. Run by the pre-commit `commit-msg` stage; argv[1] is the commit
message file.
"""

from __future__ import annotations

import re
import sys

# Kept in sync with ll-predictions' commitlint type-enum.
TYPES = ("feat", "fix", "chore", "docs", "refactor", "test", "perf", "build", "ci")

# type(optional-scope)optional-!: subject
PATTERN = re.compile(rf"^(?:{'|'.join(TYPES)})(\([^()\n]+\))?!?: .+")

# Auto-generated messages that should pass through unchecked.
EXEMPT_PREFIXES = ("Merge ", "Revert ", "fixup!", "squash!")


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    with open(sys.argv[1], encoding="utf-8") as f:
        lines = f.read().splitlines()

    subject = next((ln for ln in lines if ln.strip() and not ln.startswith("#")), "")

    if subject.startswith(EXEMPT_PREFIXES):
        return 0
    if PATTERN.match(subject):
        return 0

    print("\n✖ Commit message is not a Conventional Commit.\n")
    print(f"  subject: {subject!r}")
    print("  expected: <type>[(scope)][!]: <description>")
    print(f"  types:   {', '.join(TYPES)}")
    print("  example: feat(query): add unit-scoped retrieval\n")
    print("  See https://www.conventionalcommits.org/en/v1.0.0/\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
