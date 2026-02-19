"""
smart_match.py — Intelligent stack matching for Fix mode.

When a user pastes an error and multiple stacks contain the detected service,
this module figures out WHICH stack is most likely causing the error — so the
user never has to pick from a list.

Strategy (scored, highest wins):
  1. Dir name match: stack directory named after the service (real-world dominant)
  2. Stack completeness: stack has both *arr service AND download client
  3. Error path reachability: does the error path match volume targets?
  4. Cross-candidate uniqueness: is a volume target unique to this stack?
  5. Health correlation: stacks with problems match error types
  6. Service count: focused stacks more likely to be the service's home

Scoring uses two passes:
  - Pass 1: Gather per-stack data (volume targets, service-level volumes)
  - Pass 2: Score with cross-candidate context (uniqueness, relative specificity)

Returns a ranked list with confidence. Frontend auto-selects if confidence
is high enough, shows compact pill picker otherwise.
"""

import os
import logging
from typing import Any, Dict, List, Optional, Set

import yaml

from backend.discovery import (
    COMPOSE_FILENAMES,
    _CONFIG_TARGETS,
)

logger = logging.getLogger("maparr.smart_match")

# Known download clients — used for stack completeness check
_DOWNLOAD_CLIENTS = {"qbittorrent", "sabnzbd", "nzbget", "transmission", "deluge", "rtorrent", "jdownloader"}

# Known *arr apps that produce import/hardlink errors
_ARR_APPS = {"sonarr", "radarr", "lidarr", "readarr", "whisparr"}


def smart_match(
    parsed_error: Dict[str, Any],
    candidate_stacks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Score each candidate stack against the parsed error.

    Args:
        parsed_error: {"service": "sonarr", "path": "/data/tv/...", "error_type": "import_failed"}
        candidate_stacks: List of stack dicts from discover (must include path, volume_targets)

    Returns:
        {
            "best": stack_dict or None,
            "confidence": "high" | "medium" | "low",
            "reason": "human-readable explanation",
            "ranked": [{"stack": stack_dict, "score": int, "reasons": [str]}, ...]
        }
    """
    service = (parsed_error.get("service") or "").lower()
    error_path = (parsed_error.get("path") or "").replace("\\", "/").lower()
    error_type = (parsed_error.get("error_type") or "").lower()

    logger.info("Smart match: service=%s path=%s type=%s (%d candidates)",
                 service or "?", error_path or "?", error_type or "?",
                 len(candidate_stacks))

    if not candidate_stacks:
        return {"best": None, "confidence": "low", "reason": "No candidate stacks", "ranked": []}

    # ── Pass 1: Gather per-stack metadata ──
    # Collect all volume targets across candidates for uniqueness analysis
    all_targets_count: Dict[str, int] = {}  # target -> how many stacks have it
    stack_meta = []  # parallel list with per-stack computed data

    for stack in candidate_stacks:
        stack_path = stack.get("path", "")
        targets = [t.lower() for t in stack.get("volume_targets", [])]
        services_list = [s.lower() for s in stack.get("services", [])]

        # Track target frequency across all candidates
        for t in set(targets):  # dedupe within stack
            all_targets_count[t] = all_targets_count.get(t, 0) + 1

        # Get service-specific volume data (deeper analysis)
        svc_volumes = None
        if service:
            svc_volumes = _get_service_volumes(stack_path, service)

        # Check stack completeness: does it have both arr + download client?
        has_arr = any(app in svc for svc in services_list for app in _ARR_APPS)
        has_dl = any(dl in svc for svc in services_list for dl in _DOWNLOAD_CLIENTS)

        stack_meta.append({
            "targets": targets,
            "services_list": services_list,
            "svc_volumes": svc_volumes,
            "has_arr": has_arr,
            "has_dl": has_dl,
        })

    # ── Pass 2: Score with cross-candidate context ──
    scored = []

    for i, stack in enumerate(candidate_stacks):
        score = 0
        reasons = []
        meta = stack_meta[i]
        stack_path = stack.get("path", "")
        dir_name = os.path.basename(stack_path).lower()
        targets = meta["targets"]
        health = stack.get("health", "unknown")
        svc_count = stack.get("service_count", 0)
        svc_volumes = meta["svc_volumes"]

        # ── Signal 1: Dir name matches service (dominant in real-world) ──
        if dir_name == service:
            score += 100
            reasons.append("Stack directory matches service name")
        elif service and service in dir_name:
            score += 50
            reasons.append("Stack directory contains service name")

        # ── Signal 2: Stack completeness ──
        # Import/hardlink errors happen at the arr↔download-client boundary.
        # A stack with both is much more likely to be "the" stack producing the error.
        is_import_error = error_type in ("import_failed", "path_unreachable", "path_not_found", "remote_path_mapping")
        is_hardlink_error = error_type in ("cross_device_link", "hardlink_failed")

        if meta["has_arr"] and meta["has_dl"]:
            if is_import_error or is_hardlink_error:
                score += 20
                reasons.append("Stack has both *arr app and download client")
            else:
                score += 10
                reasons.append("Complete media stack")

        # ── Signal 3: Error path vs volume targets (with uniqueness bonus) ──
        # Use service-specific volumes when available (more precise), fall back to stack-level
        vol_check_targets = svc_volumes if svc_volumes is not None else targets
        vol_source = "service" if svc_volumes is not None else "stack"

        if error_path and vol_check_targets:
            best_target = ""
            best_target_len = 0
            for t in vol_check_targets:
                if error_path == t or error_path.startswith(t + "/"):
                    if len(t) > best_target_len:
                        best_target = t
                        best_target_len = len(t)

            if best_target_len > 0:
                # Path IS reachable — score by specificity
                base_score = 10 + best_target_len
                score += base_score
                reasons.append(f"Error path reachable via {vol_source} volume (specificity {best_target_len})")

                # Uniqueness bonus: if this target is rare among candidates, strong signal
                target_freq = all_targets_count.get(best_target, 1)
                if target_freq == 1:
                    score += 30
                    reasons.append(f"Volume target '{best_target}' is unique to this stack")
                elif target_freq <= 2:
                    score += 15
                    reasons.append(f"Volume target '{best_target}' is rare ({target_freq} stacks)")
            else:
                # Path NOT reachable — for import_failed, this IS the expected pattern
                # (the error says the path doesn't exist in the container)
                if is_import_error:
                    score += 15
                    reasons.append(f"Error path unreachable at {vol_source} level (consistent with error type)")

        # ── Signal 4: Health correlation ──
        if health == "problem":
            score += 15
            reasons.append("Stack has known volume issues")
            if is_hardlink_error:
                score += 20
                reasons.append("Error type matches stack's volume conflict pattern")
        elif health == "warning":
            score += 5
            reasons.append("Stack needs review")

        # ── Signal 5: Focused stack ──
        if svc_count == 1:
            score += 8
            reasons.append("Single-service stack")
        elif svc_count <= 3:
            score += 4
            reasons.append("Focused stack")

        logger.info("Smart match: %s → score=%d (%s)",
                     dir_name, score, "; ".join(reasons) if reasons else "no signals")
        scored.append({"stack": stack, "score": score, "reasons": reasons})

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    best = scored[0]
    runner_up = scored[1] if len(scored) > 1 else {"score": 0}

    # Determine confidence
    gap = best["score"] - runner_up["score"]
    if best["score"] >= 80 and gap >= 15:
        confidence = "high"
    elif best["score"] >= 30 and gap >= 10:
        confidence = "medium"
    else:
        confidence = "low"

    reason = "; ".join(best["reasons"]) if best["reasons"] else "Best available match"

    logger.info(
        "Smart match: best=%s (score=%d), runner_up=%d, gap=%d, confidence=%s",
        os.path.basename(best["stack"].get("path", "")),
        best["score"],
        runner_up["score"],
        gap,
        confidence,
    )

    return {
        "best": best["stack"],
        "confidence": confidence,
        "reason": reason,
        "ranked": scored,
    }


def _get_service_volumes(stack_path: str, service_name: str) -> Optional[List[str]]:
    """
    Parse the compose file and extract container-side volume targets
    for a specific service. Returns None if parsing fails or service not found.
    """
    compose_path = None
    for fname in COMPOSE_FILENAMES:
        candidate = os.path.join(stack_path, fname)
        if os.path.isfile(candidate):
            compose_path = candidate
            break

    if not compose_path:
        return None

    try:
        with open(compose_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        services = data.get("services", {})
        if not isinstance(services, dict):
            return None

        # Find the service (case-insensitive)
        svc_config = None
        for name, config in services.items():
            if name.lower() == service_name.lower():
                svc_config = config
                break

        if not svc_config or not isinstance(svc_config, dict):
            return None

        targets = []
        for vol in svc_config.get("volumes", []):
            target = ""
            if isinstance(vol, str):
                parts = vol.split(":")
                if len(parts) < 2:
                    continue
                # Windows paths (C:\path:container:opts)
                if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
                    target = parts[2]
                # NFS syntax: hostname:/remote:/container (hostname has no /)
                elif len(parts) >= 3 and "/" not in parts[0] and "/" in parts[1]:
                    target = parts[2]
                else:
                    target = parts[1]
            elif isinstance(vol, dict):
                target = vol.get("target", "")

            if not target:
                continue
            target_clean = target.rstrip("/").split(":")[0]  # strip :ro etc
            if any(target_clean == c or target_clean.startswith(c + "/") for c in _CONFIG_TARGETS):
                continue
            if target_clean:
                targets.append(target_clean.lower())

        return targets

    except Exception:
        return None
