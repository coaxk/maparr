"""
parser.py — Smart error text parser for MapArr.

Extracts service name, filesystem path, and error type from user-pasted
error messages. Designed to be forgiving: partial input, typos, and
garbage text should never dead-end the user.

Confidence levels:
  high   — service + path clearly extracted
  medium — one of service or path extracted
  low    — guessing from keywords; user should browse stacks manually
  none   — truly unparseable; still allow user to proceed

The parser NEVER blocks the user flow. Even "none" confidence returns
a result that lets the UI proceed to stack selection.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("maparr.parser")


def _get_known_services() -> list[str]:
    """Get all known service keywords from the Image Registry.

    Lazy import to avoid circular dependency (registry initialized in main.py).
    Falls back to a minimal hardcoded list if the registry isn't loaded yet
    (e.g., during unit tests that import parser directly).
    """
    try:
        from backend.image_registry import get_registry
        return sorted(get_registry().known_keywords())
    except (ImportError, AttributeError):
        return [
            "sonarr", "radarr", "lidarr", "readarr", "whisparr",
            "prowlarr", "bazarr", "overseerr", "jellyseerr",
            "qbittorrent", "sabnzbd", "nzbget", "transmission",
            "deluge", "rtorrent", "jdownloader", "plex", "jellyfin", "emby",
        ]


@dataclass
class ParsedError:
    """Result of parsing user error input."""
    service: Optional[str] = None       # Detected service name (e.g., "sonarr")
    path: Optional[str] = None          # Extracted filesystem path
    error_type: Optional[str] = None    # Classified error type
    confidence: str = "none"            # "high", "medium", "low", "none"
    raw_input: str = ""                 # Original user input
    suggestions: list = field(default_factory=list)  # Helpful hints for the user

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "path": self.path,
            "error_type": self.error_type,
            "confidence": self.confidence,
            "raw_input": self.raw_input,
            "suggestions": self.suggestions,
        }


# ─── Multi-Error Splitting ───

# Delimiters that indicate separate errors in user-pasted text.
# Users often paste multiple log lines, Activity > Queue entries, or
# System > Status health checks in one block.
_ERROR_SPLIT_PATTERNS = [
    r'\n\s*\n',                          # Double newline (paragraph break)
    r'\n(?=\[(?:WARN|ERROR|INFO)\])',     # Log-style lines: [WARN] ... [ERROR] ...
    r'\n(?=(?:Import|Download)\s+(?:failed|error))',  # Repeated error prefixes
]

_SPLIT_REGEX = re.compile('|'.join(_ERROR_SPLIT_PATTERNS), re.IGNORECASE)


def split_errors(text: str) -> list[str]:
    """
    Split user input into individual error blocks.

    Returns a list of non-empty stripped strings. If no split points are
    found, returns the original text as a single-element list.
    """
    if not text or not text.strip():
        return []

    # Normalize Windows CRLF to Unix LF before splitting.
    # Users on Windows (and web browsers on Windows) paste text with \r\n
    # which breaks our \n-based split patterns.
    normalized = text.strip().replace("\r\n", "\n").replace("\r", "\n")

    chunks = _SPLIT_REGEX.split(normalized)
    # Filter out empty/whitespace-only chunks and very short fragments
    result = [c.strip() for c in chunks if c and c.strip() and len(c.strip()) > 10]

    return result if result else [text.strip()]


def parse_errors(text: str) -> list[dict]:
    """
    Parse potentially multiple errors from user input.

    Returns a list of ParsedError dicts. Each has an additional 'index'
    field (0-based) and 'excerpt' field (first 80 chars for UI display).
    """
    chunks = split_errors(text)
    results = []

    for i, chunk in enumerate(chunks):
        parsed = parse_error(chunk)
        d = parsed.to_dict()
        d["index"] = i
        d["excerpt"] = chunk[:80] + ("..." if len(chunk) > 80 else "")
        results.append(d)

    # Deduplicate results where the same (service, path, error_type) tuple
    # appears more than once — e.g., user pasted the same error twice.
    # Keep the first occurrence (best excerpt/index).
    seen: set[tuple] = set()
    deduped = []
    for r in results:
        key = (r["service"], r["path"], r["error_type"])
        if key in seen:
            logger.info("Dedup: dropping duplicate (service=%s, path=%s, error_type=%s) at index %d",
                        r["service"], r["path"], r["error_type"], r["index"])
            continue
        seen.add(key)
        deduped.append(r)

    if len(deduped) < len(results):
        logger.info("Dedup removed %d duplicate(s): %d → %d results",
                    len(results) - len(deduped), len(results), len(deduped))
    results = deduped

    logger.info("Multi-error parse: %d chunk%s from %d chars input",
                len(results), "s" if len(results) != 1 else "", len(text))

    return results


def parse_error(text: str) -> ParsedError:
    """
    Parse user-provided error text and extract actionable information.

    Always returns a ParsedError — never raises, never returns None.
    The confidence field tells the UI how much we understood.
    """
    result = ParsedError(raw_input=text)

    if not text or not text.strip():
        result.suggestions = ["Paste the error message from your *arr app."]
        return result

    text = text.strip()
    logger.info("Parsing error text (%d chars): %.80s%s",
                 len(text), text, "..." if len(text) > 80 else "")

    # Extract components
    result.service = _extract_service(text)
    result.path = _extract_path(text)
    result.error_type = _extract_error_type(text)

    # If no explicit service name found, infer from error context clues
    if not result.service:
        result.service = _infer_service_from_context(text, result.error_type)

    if result.service:
        logger.info("Detected service: %s", result.service)
    if result.path:
        logger.info("Extracted path: %s", result.path)
    if result.error_type:
        logger.info("Classified error type: %s", result.error_type)

    # Calculate confidence
    if result.service and result.path:
        result.confidence = "high"
    elif result.service or result.path:
        result.confidence = "medium"
        if not result.service:
            result.suggestions.append(
                "Which app is showing this error? (Sonarr, Radarr, etc.)"
            )
        if not result.path:
            result.suggestions.append(
                "Can you include the full error message with the file path?"
            )
    else:
        # Try keyword detection as last resort
        if _has_path_keywords(text):
            result.confidence = "low"
            result.suggestions.append(
                "We detected path-related keywords. Select your stack below "
                "and we'll scan for issues."
            )
        else:
            result.confidence = "none"
            result.suggestions.append(
                "We couldn't extract details from that input. "
                "No worries — select your stack below and we'll scan everything."
            )

    logger.info("Parse result: confidence=%s", result.confidence)
    return result


def _extract_service(text: str) -> Optional[str]:
    """
    Extract a known service name from error text.

    Checks service names longest-first (so "nzbget" matches before "nzb"),
    then returns the earliest match position (so "sonarr and radarr" returns
    "sonarr"). Case-insensitive.
    """
    text_lower = text.lower()

    # Abbreviation → canonical service name.
    # These are checked alongside primary keywords but map to the canonical name.
    _CANONICAL = {
        "qbit": "qbittorrent",
        "sab": "sabnzbd",
        "nzb": "sabnzbd",
        "jdown": "jdownloader",
        "jd2": "jdownloader",
    }

    # Sort longest-first so "nzbget" matches before "nzb", "qbittorrent"
    # before "qbit", etc. This prevents short abbreviations from stealing
    # matches from full service names.
    services = sorted(_get_known_services(), key=len, reverse=True)

    # Find the earliest match by position in the text
    best_match = None
    best_pos = len(text_lower)
    for service in services:
        pos = text_lower.find(service)
        if pos != -1 and pos < best_pos:
            best_pos = pos
            best_match = _CANONICAL.get(service, service)

    return best_match


def _infer_service_from_context(text: str, error_type: str) -> Optional[str]:
    """
    When no service name is found in error text, infer from context clues.

    Many *arr app errors don't include the service name. We return "*arr"
    as a generic indicator — the frontend matches it against any arr-role
    service in the user's pipeline.
    """
    text_lower = text.lower()

    # "Episode file path" → Sonarr specifically
    if "episode file path" in text_lower:
        return "sonarr"

    # "Movie file path" → Radarr specifically
    if "movie file path" in text_lower:
        return "radarr"

    # EXDEV / cross-device link → arr app during import
    if "exdev" in text_lower or "cross-device link" in text_lower:
        return "*arr"

    # Remote Path Mapping → arr app
    if "remote path mapping" in text_lower:
        return "*arr"

    # "Import failed" without explicit service → arr app
    if "import failed" in text_lower:
        return "*arr"

    # "No files found are eligible for import" → arr app
    if "eligible for import" in text_lower or ("no files found" in text_lower and "import" in text_lower):
        return "*arr"

    return None


def _extract_path(text: str) -> Optional[str]:
    """
    Extract a filesystem path from error text.

    Handles:
      - Unix absolute paths: /data/tv, /mnt/nas/media
      - Windows paths: C:\\data, D:\\media
      - UNC paths: \\\\server\\share
      - Relative-looking container paths: data/tv (less confident)

    Returns the first path found. Prefers absolute paths over relative ones.
    """
    # Unix absolute paths (most common in Docker errors)
    unix_matches = re.findall(r'(?:/[a-zA-Z0-9._\-]+)+', text)

    # Windows absolute paths
    win_matches = re.findall(r'[A-Za-z]:\\(?:[a-zA-Z0-9_.\-\\]+)*', text)

    # UNC paths
    unc_matches = re.findall(r'\\\\[a-zA-Z0-9.\-]+\\[a-zA-Z0-9_.\-\\]*', text)

    # Prefer in order: UNC > Windows > Unix (most specific first)
    all_paths = unc_matches + win_matches + unix_matches

    if not all_paths:
        return None

    # Filter out obviously-not-paths (single component like "/v" from URLs)
    real_paths = [p for p in all_paths if len(p) > 3]
    return real_paths[0] if real_paths else all_paths[0]


def _extract_error_type(text: str) -> Optional[str]:
    """
    Classify the error type from keywords in the text.

    Returns a machine-readable error type that the analysis engine can use
    to prioritize which checks to run.
    """
    text_lower = text.lower()

    # Order matters: check more specific patterns first
    patterns = [
        ("import_failed", ["cannot import", "import failed", "failed to import",
                          "no files found are eligible for import"]),
        ("remote_path_mapping", ["remote path mapping", "remote path mappings",
                                "not a valid local path", "you may need a remote path mapping",
                                "not a valid *nix path"]),
        ("path_not_found", ["not found", "not exist", "no such file", "does not exist",
                          "missing root folder", "does not appear to exist inside the container"]),
        ("permission_denied", ["permission denied", "access denied", "permission", "cannot access"]),
        ("mount_issue", ["mount", "unmount", "not mounted"]),
        ("hardlink_failed", ["hardlink", "hard link", "atomic move", "cross-device",
                            "cross-device link", "invalid cross-device link", "exdev"]),
        ("disk_space", ["no space", "disk full", "insufficient space"]),
    ]

    for error_type, keywords in patterns:
        for keyword in keywords:
            if keyword in text_lower:
                return error_type

    return None


def _has_path_keywords(text: str) -> bool:
    """Check if text contains path-related vocabulary even without an extractable path."""
    text_lower = text.lower()
    keywords = [
        "path", "volume", "mount", "directory", "folder", "file",
        "import", "download", "media", "data", "config",
    ]
    return any(k in text_lower for k in keywords)
