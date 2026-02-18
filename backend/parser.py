"""
parser.py — Smart error text parser for MapArr v1.0.

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

import re
from dataclasses import dataclass, field
from typing import Optional


# Known *arr apps and download clients, ordered by frequency in support requests.
# Used for both exact match and fuzzy detection.
ARR_APPS = [
    "sonarr", "radarr", "lidarr", "readarr", "whisparr",
    "prowlarr", "bazarr", "overseerr", "jellyseerr",
]

DOWNLOAD_CLIENTS = [
    "qbittorrent", "sabnzbd", "nzbget", "transmission",
    "deluge", "rtorrent", "jdownloader",
]

MEDIA_SERVERS = [
    "plex", "jellyfin", "emby",
]

ALL_KNOWN_SERVICES = ARR_APPS + DOWNLOAD_CLIENTS + MEDIA_SERVERS


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

    # Extract components
    result.service = _extract_service(text)
    result.path = _extract_path(text)
    result.error_type = _extract_error_type(text)

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

    return result


def _extract_service(text: str) -> Optional[str]:
    """
    Extract a known service name from error text.

    Checks exact matches first, then common variations (e.g., "qbit" for
    qbittorrent). Case-insensitive.
    """
    text_lower = text.lower()

    # Exact match against known services
    for service in ALL_KNOWN_SERVICES:
        if service in text_lower:
            return service

    # Common abbreviations and typos
    abbreviations = {
        "qbit": "qbittorrent",
        "sab": "sabnzbd",
        "nzb": "sabnzbd",
        "rtorrent": "rtorrent",
        "jdown": "jdownloader",
        "jd2": "jdownloader",
    }
    for abbrev, full_name in abbreviations.items():
        if abbrev in text_lower:
            return full_name

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
