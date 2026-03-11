"""
Multi-file apply fix — batch apply corrected YAML to multiple compose files.

Validates all files before writing any. Creates backups for all files first.
If any validation fails, no files are written.
"""
import errno
import logging
import os
import shutil
from pathlib import Path
from typing import List

import yaml

from backend.resolver import COMPOSE_FILENAMES  # Single source of truth

logger = logging.getLogger(__name__)


def _safe_os_error(e: OSError, action: str) -> str:
    """User-friendly OS error without leaking internals."""
    if e.errno == errno.EACCES:
        return f"{action}: permission denied"
    if e.errno == errno.ENOSPC:
        return f"{action}: disk full"
    if e.errno == errno.EROFS:
        return f"{action}: read-only filesystem"
    if e.errno == errno.ENOENT:
        return f"{action}: file not found"
    return f"{action}: system error (check logs for details)"


def validate_fixes_batch(fixes: List[dict], stacks_root: str) -> List[dict]:
    """
    Validate all fixes before any writes.

    Returns list of error dicts. Empty list = all valid.
    Each error: {"compose_file_path": str, "error": str}
    """
    errors = []
    root = Path(stacks_root).resolve()

    for fix in fixes:
        path_str = fix.get("compose_file_path", "").strip()
        corrected = fix.get("corrected_yaml", "").strip()

        if not path_str:
            errors.append({"compose_file_path": path_str, "error": "Empty path"})
            continue

        path = Path(path_str)

        # File exists?
        if not path.is_file():
            errors.append({"compose_file_path": path_str, "error": f"File not found: {path.name}"})
            continue

        # Within stacks root?
        try:
            path.resolve().relative_to(root)
        except ValueError:
            logger.warning(
                "Path boundary check failed: path=%s resolved=%s root=%s",
                path_str, path.resolve(), root,
            )
            errors.append({"compose_file_path": path_str, "error": "Path outside stacks directory. Check MAPARR_STACKS_PATH or use Change Path to update."})
            continue

        # Valid compose filename?
        if path.name not in COMPOSE_FILENAMES:
            errors.append({"compose_file_path": path_str, "error": f"Not a recognised compose file: {path.name}. Valid names: {', '.join(sorted(COMPOSE_FILENAMES))}"})
            continue

        # Valid YAML with services key?
        if not corrected:
            errors.append({"compose_file_path": path_str, "error": "Empty corrected YAML"})
            continue

        try:
            parsed = yaml.safe_load(corrected)
            if not isinstance(parsed, dict) or "services" not in parsed:
                errors.append({"compose_file_path": path_str, "error": "Corrected YAML missing services key"})
                continue
        except yaml.YAMLError as e:
            mark = getattr(e, 'problem_mark', None)
            if mark:
                errors.append({"compose_file_path": path_str, "error": f"Invalid YAML (line {mark.line + 1}, column {mark.column + 1}): check indentation and syntax"})
            else:
                errors.append({"compose_file_path": path_str, "error": "Invalid YAML: check indentation and syntax"})
            continue

    return errors


def apply_fixes_batch(fixes: List[dict], stacks_root: str) -> dict:
    """
    Apply corrected YAML to multiple compose files.

    Strategy: validate all → backup all → write all.
    If validation fails, no files are touched.
    If a write fails mid-batch, already-written files stay written,
    backups remain for manual recovery.

    Returns:
        {
            "status": "applied" | "validation_failed" | "partial",
            "applied_count": int,
            "failed_count": int,
            "results": [{"compose_file_path", "status", "backup_file", "error?"}, ...],
            "errors": [{"compose_file_path", "error"}, ...]  (validation errors only)
        }
    """
    if not fixes:
        return {"status": "applied", "applied_count": 0, "failed_count": 0, "results": [], "errors": []}

    # Phase 1: Validate all
    errors = validate_fixes_batch(fixes, stacks_root)
    if errors:
        return {"status": "validation_failed", "applied_count": 0, "failed_count": len(errors), "results": [], "errors": errors}

    # Phase 2: Create all backups
    backups = {}
    for fix in fixes:
        path_str = fix["compose_file_path"]
        backup_path = path_str + ".bak"
        try:
            shutil.copy2(path_str, backup_path)
            backups[path_str] = backup_path
        except OSError as e:
            logger.error("Failed to create backup for %s: %s", path_str, e)
            return {
                "status": "partial",
                "applied_count": 0,
                "failed_count": len(fixes),
                "results": [{"compose_file_path": path_str, "status": "backup_failed", "error": _safe_os_error(e, "Backup failed")}],
                "errors": [],
            }

    # Phase 3: Write all files
    results = []
    applied = 0
    failed = 0

    for fix in fixes:
        path_str = fix["compose_file_path"]
        corrected = fix["corrected_yaml"].replace("\r\n", "\n")
        try:
            with open(path_str, "w", encoding="utf-8", newline="") as f:
                f.write(corrected)
            results.append({
                "compose_file_path": path_str,
                "status": "applied",
                "compose_file": os.path.basename(path_str),
                "backup_file": os.path.basename(backups[path_str]),
            })
            applied += 1
            logger.info("Applied fix to %s (backup: %s)", path_str, backups[path_str])
        except OSError as e:
            results.append({
                "compose_file_path": path_str,
                "status": "failed",
                "error": _safe_os_error(e, "Write failed"),
            })
            failed += 1
            logger.error("Failed to write %s: %s", path_str, e)

    status = "applied" if failed == 0 else "partial" if applied > 0 else "failed"
    return {
        "status": status,
        "applied_count": applied,
        "failed_count": failed,
        "results": results,
        "errors": [],
    }
