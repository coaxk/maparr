#!/usr/bin/env python3
"""Snapshot real Docker Compose stacks with sensitive values redacted.

Copies compose files from a source directory, strips API keys / passwords /
tokens, and writes sanitized copies to tests/e2e/fixtures/real-snapshot/.

Usage:
    python scripts/snapshot_real_stacks.py /path/to/stacks
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

# Compose file names we look for.
COMPOSE_FILENAMES = {
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}

# Environment variable name patterns whose values must be redacted.
_SECRET_PATTERNS: list[str] = [
    "API_KEY",
    "APIKEY",
    "PASSWORD",
    "PASSWD",
    "PASS",
    "TOKEN",
    "SECRET",
    "AUTH",
    "PRIVATE_KEY",
    "DB_PASS",
    "MYSQL_ROOT_PASSWORD",
    "POSTGRES_PASSWORD",
]

# Pre-compiled regex that matches a key name containing any secret pattern.
# Handles both list-style  (- KEY=value)  and mapping-style  (KEY: value).
#
#   List style:   "      - SONARR_API_KEY=abc123"
#   Mapping style:"      SONARR_API_KEY: abc123"
#
# We capture everything up to and including the delimiter (= or :+space) so we
# can replace only the value portion.
_SECRET_RE = re.compile(
    r"^(?P<prefix>\s*-?\s*\w*(?:"
    + "|".join(re.escape(p) for p in _SECRET_PATTERNS)
    + r")\w*\s*[:=]\s*)(?P<value>.+)$",
    re.IGNORECASE | re.MULTILINE,
)

REDACTED = "REDACTED_FOR_TESTING"


def _project_root() -> Path:
    """Return the MapArr project root (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def _destination_dir() -> Path:
    return _project_root() / "tests" / "e2e" / "fixtures" / "real-snapshot"


def _sanitize(text: str) -> str:
    """Replace secret values with the redaction placeholder."""
    return _SECRET_RE.sub(rf"\g<prefix>{REDACTED}", text)


def _discover_compose_files(source: Path) -> list[Path]:
    """Find compose files in *source* and one level deeper (cluster layout)."""
    found: list[Path] = []

    # Top-level stacks: source/<stack>/compose.yml
    for child in sorted(source.iterdir()):
        if not child.is_dir():
            continue
        for name in COMPOSE_FILENAMES:
            candidate = child / name
            if candidate.is_file():
                found.append(candidate)

        # One level deeper for cluster layouts: source/<stack>/<service>/compose.yml
        for grandchild in sorted(child.iterdir()):
            if not grandchild.is_dir():
                continue
            for name in COMPOSE_FILENAMES:
                candidate = grandchild / name
                if candidate.is_file():
                    found.append(candidate)

    return found


def snapshot(source: Path) -> None:
    """Copy and sanitize compose files from *source* into the fixture dir."""
    dest = _destination_dir()

    if dest.exists():
        print(
            f"ERROR: Destination already exists — delete it first:\n"
            f"  rm -rf {dest}",
            file=sys.stderr,
        )
        sys.exit(1)

    compose_files = _discover_compose_files(source)
    if not compose_files:
        print(f"No compose files found under {source}", file=sys.stderr)
        sys.exit(1)

    for cf in compose_files:
        # Preserve directory structure relative to source.
        rel = cf.relative_to(source)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)

        raw = cf.read_text(encoding="utf-8", errors="replace")
        sanitized = _sanitize(raw)
        out.write_text(sanitized, encoding="utf-8")
        print(f"  copied: {rel}")

    print(f"\n{len(compose_files)} file(s) → {dest}")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print(
            "Usage: python scripts/snapshot_real_stacks.py /path/to/stacks",
            file=sys.stderr,
        )
        sys.exit(2 if len(sys.argv) != 2 else 0)

    source = Path(sys.argv[1]).resolve()
    if not source.is_dir():
        print(f"ERROR: Not a directory: {source}", file=sys.stderr)
        sys.exit(1)

    snapshot(source)


if __name__ == "__main__":
    main()
