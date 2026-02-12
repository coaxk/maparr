"""
MapArr v1.0 - Path Mapping Intelligence Backend
Full backend: Docker detection, path analysis, conflict resolution,
platform-specific recommendations, SQLite persistence, *arr config detection.
"""

import os
import re
import json
import sqlite3
import logging
import platform as platform_mod
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import StreamingResponse
import asyncio
import uuid
import json
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import docker
from docker.errors import DockerException
import yaml

# ═══════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════

LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("maparr")

# ═══════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════

class ManualPathEntry(BaseModel):
    container_name: str = Field(..., description="Name of the container")
    host_path: str = Field(..., description="Path on the host machine")
    container_path: str = Field(..., description="Path inside the container")
    platform: Optional[str] = Field(None, description="Platform hint: windows|unraid|synology|linux")

class ManualPathBatch(BaseModel):
    entries: List[ManualPathEntry]
    platform: Optional[str] = None

class ConflictResolution(BaseModel):
    conflict_id: int = Field(..., description="Index of the conflict to resolve")
    chosen_source: str = Field(..., description="The source path to standardize on")

# ═══════════════════════════════════════════════════════════
# SQLITE PERSISTENCE
# ═══════════════════════════════════════════════════════════

DB_PATH = os.getenv("MAPARR_DB", "/data/maparr.db")

class Database:
    """SQLite persistence for analyses, mappings, and manual entries."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self):
        d = os.path.dirname(self.db_path)
        if d:
            os.makedirs(d, exist_ok=True)

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    platform TEXT,
                    result_json TEXT NOT NULL,
                    containers_count INTEGER DEFAULT 0,
                    conflicts_count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    mapping_json TEXT NOT NULL,
                    notes TEXT
                );
                CREATE TABLE IF NOT EXISTS manual_paths (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    container_name TEXT NOT NULL,
                    host_path TEXT NOT NULL,
                    container_path TEXT NOT NULL,
                    platform TEXT
                );
            """)
        logger.info(f"Database initialized at {self.db_path}")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # -- Analyses --
    def save_analysis(self, analysis: Dict[str, Any]) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO analyses (timestamp, platform, result_json, containers_count, conflicts_count) VALUES (?,?,?,?,?)",
                (
                    datetime.now().isoformat(),
                    analysis.get("platform", "unknown"),
                    json.dumps(analysis),
                    analysis.get("summary", {}).get("containers_analyzed", 0),
                    analysis.get("summary", {}).get("conflicts_found", 0),
                ),
            )
            return cur.lastrowid

    def get_analyses(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, platform, containers_count, conflicts_count FROM analyses ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_analysis(self, analysis_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
            if row:
                result = dict(row)
                result["result"] = json.loads(result.pop("result_json"))
                return result
            return None

    # -- Mappings --
    def save_mapping(self, mapping: Dict[str, Any], notes: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO mappings (timestamp, mapping_json, notes) VALUES (?,?,?)",
                (datetime.now().isoformat(), json.dumps(mapping), notes),
            )
            return cur.lastrowid

    def get_mappings(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM mappings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["mapping"] = json.loads(d.pop("mapping_json"))
                results.append(d)
            return results

    # -- Manual paths --
    def save_manual_path(self, entry: ManualPathEntry) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO manual_paths (timestamp, container_name, host_path, container_path, platform) VALUES (?,?,?,?,?)",
                (datetime.now().isoformat(), entry.container_name, entry.host_path, entry.container_path, entry.platform),
            )
            return cur.lastrowid

    def get_manual_paths(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM manual_paths ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_manual_path(self, path_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM manual_paths WHERE id = ?", (path_id,))
            return cur.rowcount > 0


# ═══════════════════════════════════════════════════════════
# FASTAPI APP SETUP
# ═══════════════════════════════════════════════════════════

app = FastAPI(
    title="MapArr v1.0",
    description="Path mapping intelligence for *arr applications",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════════════
# DOCKER CONNECTION MANAGER
# ═══════════════════════════════════════════════════════════

class DockerManager:
    """Manages Docker connection and container discovery."""

    def __init__(self):
        self.client: Optional[docker.DockerClient] = None
        self.is_connected = False
        self.connection_method = None
        self.error = None
        self._connect()

    def _connect(self):
        """Attempt Docker connection with smart detection."""
        attempts = [
            ("unix_socket", {"base_url": "unix:///var/run/docker.sock"}),
            ("windows_pipe", {"base_url": "npipe:////./pipe/docker_engine"}),
            ("docker_host_env", {}),
        ]

        for method, kwargs in attempts:
            try:
                self.client = docker.DockerClient(**kwargs)
                self.client.ping()
                self.is_connected = True
                self.connection_method = method
                logger.info(f"Docker connected via {method}")
                return
            except Exception as e:
                logger.debug(f"{method} failed: {e}")

        self.is_connected = False
        self.error = "Could not connect to Docker. Please check docker socket mount."
        logger.warning(f"Docker connection failed: {self.error}")

    def reconnect(self) -> bool:
        """Force reconnection attempt."""
        self.is_connected = False
        self.client = None
        self.connection_method = None
        self.error = None
        self._connect()
        return self.is_connected

    def get_containers(self, all_containers: bool = False) -> List[Dict[str, Any]]:
        """Get containers and their volume mounts."""
        if not self.is_connected:
            return []

        try:
            containers = self.client.containers.list(all=all_containers)
            result = []

            for container in containers:
                container_info = {
                    "id": container.short_id,
                    "name": container.name,
                    "image": container.image.tags[0] if container.image.tags else "unknown",
                    "status": container.status,
                    "volumes": self._extract_volumes(container),
                    "env_vars": self._extract_env_vars(container),
                    "labels": self._extract_labels(container),
                    "is_arr_app": self._is_arr_app(container),
                }
                result.append(container_info)

            logger.info(f"Found {len(result)} containers")
            return result

        except Exception as e:
            logger.error(f"Error getting containers: {e}")
            return []

    def _extract_volumes(self, container) -> Dict[str, str]:
        volumes = {}
        try:
            mounts = container.attrs.get("Mounts", [])
            for mount in mounts:
                source = mount.get("Source", "")
                destination = mount.get("Destination", "")
                if source and destination:
                    volumes[destination] = source
        except Exception as e:
            logger.debug(f"Error extracting volumes for {container.name}: {e}")
        return volumes

    def _extract_env_vars(self, container) -> Dict[str, str]:
        env_vars = {}
        path_keywords = ["path", "root", "mount", "dir", "folder", "data", "media", "download", "config"]
        try:
            config = container.attrs.get("Config", {})
            env = config.get("Env", [])
            for var in env:
                if "=" in var:
                    key, value = var.split("=", 1)
                    if any(kw in key.lower() for kw in path_keywords):
                        env_vars[key] = value
        except Exception as e:
            logger.debug(f"Error extracting env vars for {container.name}: {e}")
        return env_vars

    def _extract_labels(self, container) -> Dict[str, str]:
        try:
            return container.labels or {}
        except Exception:
            return {}

    def _is_arr_app(self, container) -> bool:
        """Detect if container is a known *arr application."""
        arr_names = [
            "sonarr", "radarr", "lidarr", "readarr", "whisparr",
            "prowlarr", "bazarr", "overseerr", "requestrr",
        ]
        name_lower = container.name.lower()
        image_str = ""
        try:
            image_str = (container.image.tags[0] if container.image.tags else "").lower()
        except Exception:
            pass
        return any(a in name_lower or a in image_str for a in arr_names)


# ═══════════════════════════════════════════════════════════
# ARR CONFIG DETECTOR
# ═══════════════════════════════════════════════════════════

class ArrConfigDetector:
    """Detects *arr app configurations from env vars and volume mounts."""

    ARR_ENV_PATTERNS = {
        "sonarr": {"root_folder": "/tv", "typical_port": 8989},
        "radarr": {"root_folder": "/movies", "typical_port": 7878},
        "lidarr": {"root_folder": "/music", "typical_port": 8686},
        "readarr": {"root_folder": "/books", "typical_port": 8787},
        "whisparr": {"root_folder": "/xxx", "typical_port": 6969},
        "prowlarr": {"root_folder": None, "typical_port": 9696},
        "bazarr": {"root_folder": None, "typical_port": 6767},
    }

    DOWNLOAD_CLIENT_PATHS = ["/downloads", "/data/downloads", "/data/usenet", "/data/torrents"]

    @classmethod
    def detect_arr_configs(cls, containers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        configs = []
        for c in containers:
            if not c.get("is_arr_app"):
                continue

            app_type = cls._identify_arr_type(c["name"], c.get("image", ""))
            if not app_type:
                continue

            expected = cls.ARR_ENV_PATTERNS.get(app_type, {})
            root_folder = expected.get("root_folder")

            config = {
                "container": c["name"],
                "app_type": app_type,
                "detected_root_folder": cls._find_root_folder(c, root_folder),
                "download_paths": cls._find_download_paths(c),
                "config_path": cls._find_config_path(c),
                "issues": [],
            }

            # Check for common misconfigurations
            if root_folder and not config["detected_root_folder"]:
                config["issues"].append(
                    f"No root folder mount detected. Expected something mapping to {root_folder}"
                )

            if app_type in ("sonarr", "radarr", "lidarr", "readarr") and not config["download_paths"]:
                config["issues"].append(
                    "No download client path detected. Hardlinks/moves may not work."
                )

            configs.append(config)

        logger.info(f"Detected {len(configs)} *arr app configurations")
        return configs

    @classmethod
    def _identify_arr_type(cls, name: str, image: str) -> Optional[str]:
        combined = (name + " " + image).lower()
        for arr_name in cls.ARR_ENV_PATTERNS:
            if arr_name in combined:
                return arr_name
        return None

    @classmethod
    def _find_root_folder(cls, container: Dict, expected_dest: Optional[str]) -> Optional[str]:
        volumes = container.get("volumes", {})
        if expected_dest and expected_dest in volumes:
            return volumes[expected_dest]
        # Check env vars for root folder hints
        for key, val in container.get("env_vars", {}).items():
            if "root" in key.lower():
                return val
        return None

    @classmethod
    def _find_download_paths(cls, container: Dict) -> List[str]:
        volumes = container.get("volumes", {})
        found = []
        for dest, source in volumes.items():
            if any(dp in dest.lower() for dp in ["download", "torrent", "usenet", "nzb"]):
                found.append(source)
        return found

    @classmethod
    def _find_config_path(cls, container: Dict) -> Optional[str]:
        volumes = container.get("volumes", {})
        for dest, source in volumes.items():
            if "config" in dest.lower() or dest == "/config":
                return source
        return None


# ═══════════════════════════════════════════════════════════
# PATH ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════

class PathAnalyzer:
    """Analyzes Docker volume configurations, detects conflicts, and provides fix suggestions."""

    HARDLINK_SAFE_LAYOUTS = {
        "standard": {
            "description": "TRaSH Guides recommended single-root layout",
            "structure": {
                "/data": "Root of all media",
                "/data/torrents": "Torrent downloads",
                "/data/usenet": "Usenet downloads",
                "/data/media/movies": "Movie library",
                "/data/media/tv": "TV library",
                "/data/media/music": "Music library",
            },
        },
        "unraid": {
            "description": "Unraid-optimized layout using /mnt/user",
            "structure": {
                "/mnt/user/data": "Root of all media on array",
                "/mnt/user/data/torrents": "Torrent downloads",
                "/mnt/user/data/usenet": "Usenet downloads",
                "/mnt/user/data/media/movies": "Movie library",
                "/mnt/user/data/media/tv": "TV library",
            },
        },
        "synology": {
            "description": "Synology NAS layout using /volume1",
            "structure": {
                "/volume1/data": "Root of all media",
                "/volume1/data/torrents": "Torrent downloads",
                "/volume1/data/usenet": "Usenet downloads",
                "/volume1/data/media/movies": "Movie library",
                "/volume1/data/media/tv": "TV library",
            },
        },
    }

    def __init__(self, containers: List[Dict[str, Any]], manual_paths: Optional[List[Dict]] = None):
        self.containers = containers
        self.manual_paths = manual_paths or []
        self.platform = self._detect_platform()
        self.conflicts = []
        self.recommendations = []

    def _detect_platform(self) -> str:
        all_paths = []
        for container in self.containers:
            all_paths.extend(container["volumes"].values())
        for mp in self.manual_paths:
            all_paths.append(mp.get("host_path", ""))

        if any("\\" in path for path in all_paths):
            return "windows"
        if any("/mnt/user" in path for path in all_paths):
            return "unraid"
        if any(p in path for path in all_paths for p in ["/volume1", "/volume2", "synology"]):
            return "synology"
        if any("/mnt/c/" in path or "/mnt/d/" in path for path in all_paths):
            return "wsl2"
        if any("/var/lib/docker" in path for path in all_paths):
            return "linux"
        if any("/data" in path or "/media" in path for path in all_paths):
            return "docker"
        return "unknown"

    def analyze(self) -> Dict[str, Any]:
        self.conflicts = []
        self.recommendations = []

        self._detect_conflicts()
        self._detect_hardlink_issues()
        self._detect_permission_issues()
        self._generate_platform_recommendations()

        return {
            "platform": self.platform,
            "containers": self.containers,
            "manual_paths": self.manual_paths,
            "conflicts": self.conflicts,
            "recommendations": self.recommendations,
            "hardlink_layout": self._suggest_hardlink_layout(),
            "summary": self._generate_summary(),
            "analyzed_at": datetime.now().isoformat(),
        }

    def _detect_conflicts(self):
        # Build destination -> [(container, source)] map
        destination_map: Dict[str, List[Dict[str, str]]] = {}
        for container in self.containers:
            for dest, source in container["volumes"].items():
                destination_map.setdefault(dest, []).append(
                    {"container": container["name"], "source": source}
                )

        # Include manual paths
        for mp in self.manual_paths:
            destination_map.setdefault(mp.get("container_path", ""), []).append(
                {"container": mp.get("container_name", "manual"), "source": mp.get("host_path", "")}
            )

        # Check for different sources mapped to same destination
        for dest, mappings in destination_map.items():
            if len(mappings) > 1:
                sources = set(m["source"] for m in mappings)
                if len(sources) > 1:
                    self.conflicts.append({
                        "type": "multiple_sources",
                        "destination": dest,
                        "containers": [m["container"] for m in mappings],
                        "sources": list(sources),
                        "severity": "high",
                        "fix": {
                            "description": f"Standardize all containers to use the same host path for '{dest}'",
                            "suggested_source": self._pick_best_source(list(sources)),
                            "action": f"Update docker-compose volumes so all containers map the same host path to '{dest}'",
                        },
                    })

        # Check *arr consistency
        arr_containers = [c for c in self.containers if c.get("is_arr_app")]
        if len(arr_containers) > 1:
            self._check_arr_consistency(arr_containers)

    def _pick_best_source(self, sources: List[str]) -> str:
        """Pick the best source path from candidates based on heuristics."""
        # Prefer paths under /data, /mnt/user/data, or /volume1/data
        preferred_prefixes = ["/data", "/mnt/user/data", "/volume1/data"]
        for src in sources:
            if any(src.startswith(p) for p in preferred_prefixes):
                return src
        # Fallback: pick the shortest non-empty path
        return min(sources, key=len) if sources else sources[0]

    def _check_arr_consistency(self, arr_containers: List[Dict]):
        paths_by_container = {}
        for container in arr_containers:
            paths_by_container[container["name"]] = set(container["volumes"].keys())

        all_dests = set()
        for paths in paths_by_container.values():
            all_dests.update(paths)

        for container_name, paths in paths_by_container.items():
            if not all_dests:
                continue
            matching = len(paths & all_dests)
            if matching < len(all_dests) * 0.7:
                shared_paths = all_dests - paths
                self.conflicts.append({
                    "type": "arr_path_mismatch",
                    "container": container_name,
                    "severity": "medium",
                    "note": f"{container_name} is missing paths that other *arr apps have",
                    "missing_paths": list(shared_paths),
                    "fix": {
                        "description": f"Add the missing volume mounts to {container_name}",
                        "missing": list(shared_paths),
                        "action": "Ensure all *arr apps share the same /data mount for hardlinks to work",
                    },
                })

    def _detect_hardlink_issues(self):
        """Detect configurations that break hardlinks."""
        arr_containers = [c for c in self.containers if c.get("is_arr_app")]
        download_containers = [
            c for c in self.containers
            if any(n in c["name"].lower() for n in ["qbit", "transmission", "deluge", "nzbget", "sabnzbd", "rtorrent"])
        ]

        if not arr_containers or not download_containers:
            return

        # Collect host-side root paths for arr apps and download clients
        arr_roots = set()
        for c in arr_containers:
            for source in c["volumes"].values():
                arr_roots.add(self._get_root(source))

        dl_roots = set()
        for c in download_containers:
            for source in c["volumes"].values():
                dl_roots.add(self._get_root(source))

        # If they share no common root, hardlinks won't work
        common = arr_roots & dl_roots
        if not common and arr_roots and dl_roots:
            self.conflicts.append({
                "type": "hardlink_broken",
                "severity": "high",
                "note": "Download clients and *arr apps don't share a common root path on the host",
                "arr_roots": list(arr_roots),
                "download_roots": list(dl_roots),
                "fix": {
                    "description": "Use a single root path (e.g., /data) mapped to all containers",
                    "example": "Map /data:/data in both the *arr app and download client containers",
                    "action": "Restructure volumes so both share the same parent directory on the host",
                },
            })

    def _get_root(self, path: str) -> str:
        """Get the first two path components as the 'root'."""
        parts = path.replace("\\", "/").strip("/").split("/")
        if len(parts) >= 2:
            return "/" + "/".join(parts[:2])
        return "/" + parts[0] if parts else path

    def _detect_permission_issues(self):
        """Flag common permission-related misconfigurations."""
        for container in self.containers:
            env = container.get("env_vars", {})
            puid = env.get("PUID") or env.get("UID")
            pgid = env.get("PGID") or env.get("GID")

            if not container.get("is_arr_app"):
                continue

            if not puid or not pgid:
                self.conflicts.append({
                    "type": "permission_warning",
                    "container": container["name"],
                    "severity": "medium",
                    "note": f"{container['name']} does not have PUID/PGID set",
                    "fix": {
                        "description": "Set PUID and PGID environment variables to match your user",
                        "action": "Add PUID=1000 and PGID=1000 (adjust to your user) in environment section",
                    },
                })

        # Check that all arr apps use the same PUID/PGID
        arr_containers = [c for c in self.containers if c.get("is_arr_app")]
        uid_set = set()
        gid_set = set()
        for c in arr_containers:
            env = c.get("env_vars", {})
            uid = env.get("PUID") or env.get("UID")
            gid = env.get("PGID") or env.get("GID")
            if uid:
                uid_set.add(uid)
            if gid:
                gid_set.add(gid)

        if len(uid_set) > 1 or len(gid_set) > 1:
            self.conflicts.append({
                "type": "permission_mismatch",
                "severity": "high",
                "note": "Different *arr apps are running with different PUID/PGID values",
                "uids_found": list(uid_set),
                "gids_found": list(gid_set),
                "fix": {
                    "description": "All *arr apps and download clients should use the same PUID/PGID",
                    "action": "Pick one UID/GID pair and apply it to all containers",
                },
            })

    def _generate_platform_recommendations(self):
        """Generate platform-specific recommendations."""
        # Universal recommendations
        if self.conflicts:
            high_count = sum(1 for c in self.conflicts if c["severity"] == "high")
            if high_count:
                self.recommendations.append({
                    "priority": "high",
                    "title": "Resolve Critical Conflicts",
                    "description": f"Found {high_count} high-severity conflict(s) that need attention",
                    "action": "Review the conflicts list and apply the suggested fixes",
                })

        # Platform-specific
        if self.platform == "windows":
            self.recommendations.extend([
                {
                    "priority": "high",
                    "title": "WSL2 Path Conversion",
                    "description": "Windows paths must be converted for Docker containers running in WSL2",
                    "examples": [
                        "C:\\Users\\data -> /mnt/c/Users/data",
                        "D:\\Media -> /mnt/d/Media",
                        "\\\\NAS\\share -> //NAS/share (use CIFS mount instead)",
                    ],
                    "action": "Convert all Windows-style backslash paths to WSL2 /mnt/ format",
                },
                {
                    "priority": "medium",
                    "title": "Avoid Docker Desktop Named Volumes",
                    "description": "Named volumes in Docker Desktop for Windows are stored in WSL2 VM, not on your drives",
                    "action": "Use bind mounts with explicit host paths instead of named volumes for media",
                },
                {
                    "priority": "info",
                    "title": "Hardlinks on Windows",
                    "description": "Hardlinks work on NTFS but require source and destination on the same partition",
                    "action": "Keep all media and downloads on the same drive letter",
                },
            ])

        elif self.platform == "wsl2":
            self.recommendations.extend([
                {
                    "priority": "high",
                    "title": "WSL2 Path Performance",
                    "description": "Accessing /mnt/c or /mnt/d from WSL2 is slow due to 9P filesystem",
                    "action": "Store media on the WSL2 native filesystem (e.g., ~/data) for best performance, or accept the I/O penalty",
                },
                {
                    "priority": "medium",
                    "title": "Consistent Path References",
                    "description": "Don't mix Windows paths (C:\\) and WSL paths (/mnt/c/) in compose files",
                    "action": "Use only /mnt/c/ style paths in docker-compose.yml",
                },
            ])

        elif self.platform == "unraid":
            self.recommendations.extend([
                {
                    "priority": "high",
                    "title": "Use /mnt/user for Hardlinks",
                    "description": "On Unraid, use /mnt/user/data as the single root for all media",
                    "examples": [
                        "/mnt/user/data/media/movies -> /data/media/movies (in container)",
                        "/mnt/user/data/torrents -> /data/torrents (in container)",
                    ],
                    "action": "Map /mnt/user/data:/data in ALL media containers",
                },
                {
                    "priority": "high",
                    "title": "Avoid /mnt/user0, /mnt/disk1, etc.",
                    "description": "Direct disk paths bypass the Unraid array and break hardlinks across disks",
                    "action": "Always use /mnt/user/ (the merged view) instead of individual disk paths",
                },
                {
                    "priority": "medium",
                    "title": "Unraid Cache Drive",
                    "description": "For best download performance, keep /data/torrents on cache",
                    "action": "Set the share's 'Use cache' to 'Prefer' for the downloads folder",
                },
            ])

        elif self.platform == "synology":
            self.recommendations.extend([
                {
                    "priority": "high",
                    "title": "Synology Volume Paths",
                    "description": "Use /volume1/data as root for all media containers",
                    "examples": [
                        "/volume1/data/media -> /data/media (in container)",
                        "/volume1/data/torrents -> /data/torrents (in container)",
                    ],
                    "action": "Map /volume1/data:/data in all containers",
                },
                {
                    "priority": "medium",
                    "title": "Synology Docker Permissions",
                    "description": "Synology Docker uses specific UID/GID. Check yours with 'id' command via SSH",
                    "action": "Set PUID/PGID to match your Synology user (usually 1026/100 for first admin)",
                },
                {
                    "priority": "info",
                    "title": "Btrfs Considerations",
                    "description": "Synology uses Btrfs by default. Hardlinks work within the same volume but not across volumes",
                    "action": "Keep all media on the same volume (e.g., /volume1)",
                },
            ])

        elif self.platform in ("linux", "docker"):
            self.recommendations.extend([
                {
                    "priority": "high",
                    "title": "Single Root Data Directory",
                    "description": "Use one root directory (e.g., /data) mapped into all containers for hardlink support",
                    "examples": [
                        "/data/media/movies -> /data/media/movies",
                        "/data/torrents -> /data/torrents",
                    ],
                    "action": "Map /data:/data in all *arr and download client containers",
                },
                {
                    "priority": "medium",
                    "title": "Consistent UID/GID",
                    "description": "All containers should run with the same user to avoid permission errors",
                    "action": "Set PUID=1000 PGID=1000 (or your user's values from `id` command) in all containers",
                },
            ])

        if self.platform == "unknown":
            self.recommendations.append({
                "priority": "medium",
                "title": "Platform Not Detected",
                "description": "We couldn't auto-detect your platform from container paths",
                "action": "Use the manual path entry feature or set platform hint in your request",
            })

    def _suggest_hardlink_layout(self) -> Dict[str, Any]:
        """Suggest a hardlink-safe layout based on platform."""
        key = "standard"
        if self.platform == "unraid":
            key = "unraid"
        elif self.platform == "synology":
            key = "synology"

        layout = self.HARDLINK_SAFE_LAYOUTS[key].copy()
        layout["platform"] = self.platform
        layout["note"] = (
            "This layout ensures hardlinks/instant moves work between download clients and *arr apps. "
            "The key is that all containers see the same filesystem tree rooted at a single path."
        )
        return layout

    def _generate_summary(self) -> Dict[str, Any]:
        high = sum(1 for c in self.conflicts if c["severity"] == "high")
        medium = sum(1 for c in self.conflicts if c["severity"] == "medium")
        return {
            "platform_detected": self.platform,
            "containers_analyzed": len(self.containers),
            "manual_paths_included": len(self.manual_paths),
            "conflicts_found": high,
            "warnings_found": medium,
            "status": "healthy" if high == 0 else "needs_attention",
        }


# ═══════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════

docker_manager = DockerManager()
db = Database()
 
# Simple in-memory job store for async analysis jobs
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = asyncio.Lock()

async def _run_analysis_job(job_id: str, platform_hint: Optional[str] = None):
    """Background worker to run analysis and update job state."""
    async with jobs_lock:
        jobs[job_id].update({"status": "detecting", "progress": 5, "started_at": datetime.now().isoformat()})

    # Phase 1: detecting
    await asyncio.sleep(0.4)
    try:
        containers = docker_manager.get_containers()
        manual = db.get_manual_paths()
    except Exception as e:
        async with jobs_lock:
            jobs[job_id].update({"status": "error", "error": str(e), "progress": 0})
        return

    async with jobs_lock:
        jobs[job_id].update({"status": "analyzing", "progress": 25})

    # Phase 2: analyzing (simulate incremental progress while running analyzer)
    try:
        analyzer = PathAnalyzer(containers, manual_paths=manual)
        if platform_hint:
            analyzer.platform = platform_hint

        # simulate chunked progress
        for p in (35, 50, 65, 80):
            await asyncio.sleep(0.3)
            async with jobs_lock:
                jobs[job_id]["progress"] = p

        # Perform analysis (may be CPU-bound but quick for small sets)
        analysis = analyzer.analyze()

        # Persist the analysis
        analysis_id = db.save_analysis(analysis)
        analysis["analysis_id"] = analysis_id

        async with jobs_lock:
            jobs[job_id].update({"status": "complete", "progress": 100, "result": analysis, "finished_at": datetime.now().isoformat()})
    except Exception as e:
        logger.exception("Analysis job failed")
        async with jobs_lock:
            jobs[job_id].update({"status": "error", "error": str(e), "progress": 0})


# ═══════════════════════════════════════════════════════════
# API ENDPOINTS - Core
# ═══════════════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
        "docker_connected": docker_manager.is_connected,
    }


@app.get("/api/docker/status")
async def docker_status():
    return {
        "connected": docker_manager.is_connected,
        "method": docker_manager.connection_method,
        "error": docker_manager.error,
    }


@app.post("/api/docker/reconnect")
async def docker_reconnect():
    """Force Docker reconnection attempt."""
    success = docker_manager.reconnect()
    return {
        "connected": success,
        "method": docker_manager.connection_method,
        "error": docker_manager.error,
    }


@app.get("/api/containers")
async def list_containers(include_stopped: bool = False):
    if not docker_manager.is_connected:
        raise HTTPException(status_code=503, detail="Docker not connected. Check docker socket mount.")

    containers = docker_manager.get_containers(all_containers=include_stopped)
    return {
        "containers": containers,
        "total": len(containers),
        "arr_apps": sum(1 for c in containers if c.get("is_arr_app")),
        "timestamp": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════════════════
# API ENDPOINTS - Analysis
# ═══════════════════════════════════════════════════════════

@app.post("/api/analyze")
async def analyze_paths(platform_hint: Optional[str] = None):
    """Analyze path configurations, detect conflicts, and provide fix suggestions."""
    # Create an async job and return job id immediately
    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "created_at": datetime.now().isoformat(),
        "platform_hint": platform_hint,
    }

    # Start background task
    asyncio.create_task(_run_analysis_job(job_id, platform_hint=platform_hint))

    return {"jobId": job_id, "status": "queued"}


@app.get("/api/recommendations")
async def get_recommendations():
    if not docker_manager.is_connected:
        return {
            "recommendations": [{
                "priority": "critical",
                "title": "Connect Docker Socket",
                "description": "MapArr needs access to Docker to analyze your setup",
                "action": "Mount /var/run/docker.sock in compose file",
            }]
        }

    containers = docker_manager.get_containers()
    manual = db.get_manual_paths()
    analyzer = PathAnalyzer(containers, manual_paths=manual)
    analysis = analyzer.analyze()

    return {
        "platform": analysis["platform"],
        "recommendations": analysis["recommendations"],
        "conflicts": analysis["conflicts"],
        "hardlink_layout": analysis["hardlink_layout"],
    }


@app.get('/api/jobs')
async def list_jobs():
    async with jobs_lock:
        items = list(jobs.values())
    return {"jobs": items, "total": len(items)}


@app.get('/api/job/{job_id}')
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    return job


@app.get('/api/job/{job_id}/events')
async def job_events(job_id: str):
    """Server-Sent Events endpoint streaming job progress."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail='Job not found')

    async def event_generator():
        last_payload = None
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error':'job not found'})}\n\n"
                break
            payload = {k: job.get(k) for k in ("job_id", "status", "progress", "error", "result")}
            s = json.dumps(payload, default=str)
            if s != last_payload:
                last_payload = s
                yield f"data: {s}\n\n"
            if job.get('status') in ('complete', 'error'):
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(event_generator(), media_type='text/event-stream')


@app.get("/api/arr-configs")
async def get_arr_configs():
    """Detect *arr app configurations from running containers."""
    if not docker_manager.is_connected:
        raise HTTPException(status_code=503, detail="Docker not connected.")

    containers = docker_manager.get_containers()
    configs = ArrConfigDetector.detect_arr_configs(containers)
    return {"configs": configs, "total": len(configs)}


# ═══════════════════════════════════════════════════════════
# API ENDPOINTS - Manual Paths
# ═══════════════════════════════════════════════════════════

@app.post("/api/manual-paths")
async def add_manual_path(entry: ManualPathEntry):
    """Add a manual path entry for containers not auto-detected."""
    path_id = db.save_manual_path(entry)
    logger.info(f"Manual path #{path_id} added: {entry.container_name} {entry.host_path} -> {entry.container_path}")
    return {
        "status": "saved",
        "id": path_id,
        "entry": entry.model_dump(),
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/manual-paths/batch")
async def add_manual_paths_batch(batch: ManualPathBatch):
    """Add multiple manual path entries at once."""
    ids = []
    for entry in batch.entries:
        if batch.platform and not entry.platform:
            entry.platform = batch.platform
        pid = db.save_manual_path(entry)
        ids.append(pid)

    logger.info(f"Batch: added {len(ids)} manual paths")
    return {
        "status": "saved",
        "ids": ids,
        "count": len(ids),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/manual-paths")
async def list_manual_paths():
    """List all manual path entries."""
    paths = db.get_manual_paths()
    return {"paths": paths, "total": len(paths)}


@app.delete("/api/manual-paths/{path_id}")
async def delete_manual_path(path_id: int):
    """Delete a manual path entry."""
    deleted = db.delete_manual_path(path_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Manual path not found")
    return {"status": "deleted", "id": path_id}


# ═══════════════════════════════════════════════════════════
# API ENDPOINTS - Persistence
# ═══════════════════════════════════════════════════════════

@app.post("/api/save-mapping")
async def save_mapping(mapping: Dict[str, Any] = Body(...)):
    """Save a user's path mapping decision."""
    mapping_id = db.save_mapping(mapping, notes=mapping.get("notes", ""))
    logger.info(f"Mapping #{mapping_id} saved")
    return {
        "status": "saved",
        "id": mapping_id,
        "mapping": mapping,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/mappings")
async def list_mappings():
    """List saved mappings."""
    mappings = db.get_mappings()
    return {"mappings": mappings, "total": len(mappings)}


@app.get("/api/analyses")
async def list_analyses(limit: int = 20):
    """List past analyses."""
    analyses = db.get_analyses(limit)
    return {"analyses": analyses, "total": len(analyses)}


@app.get("/api/analyses/{analysis_id}")
async def get_analysis(analysis_id: int):
    """Get a specific past analysis by ID."""
    analysis = db.get_analysis(analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


# ═══════════════════════════════════════════════════════════
# API ENDPOINTS - Conflict Resolution
# ═══════════════════════════════════════════════════════════

@app.post("/api/resolve-conflict")
async def resolve_conflict(resolution: ConflictResolution):
    """Accept a conflict resolution and generate compose snippet."""
    # Run a fresh analysis to get current conflicts
    if not docker_manager.is_connected:
        raise HTTPException(status_code=503, detail="Docker not connected.")

    containers = docker_manager.get_containers()
    manual = db.get_manual_paths()
    analyzer = PathAnalyzer(containers, manual_paths=manual)
    analysis = analyzer.analyze()

    conflicts = analysis["conflicts"]
    if resolution.conflict_id >= len(conflicts):
        raise HTTPException(status_code=404, detail="Conflict index out of range")

    conflict = conflicts[resolution.conflict_id]

    # Generate a compose snippet showing the fix
    snippet_lines = ["# Fix for conflict: different sources to same destination"]
    snippet_lines.append(f"# Destination: {conflict.get('destination', 'N/A')}")
    snippet_lines.append(f"# Standardize on: {resolution.chosen_source}")
    snippet_lines.append("")

    for container_name in conflict.get("containers", []):
        snippet_lines.append(f"  {container_name}:")
        snippet_lines.append(f"    volumes:")
        snippet_lines.append(f"      - {resolution.chosen_source}:{conflict.get('destination', '/data')}")
        snippet_lines.append("")

    compose_snippet = "\n".join(snippet_lines)

    # Save this resolution as a mapping
    db.save_mapping({
        "conflict": conflict,
        "resolution": resolution.model_dump(),
        "compose_snippet": compose_snippet,
    }, notes=f"Resolved conflict #{resolution.conflict_id}")

    return {
        "status": "resolved",
        "conflict": conflict,
        "chosen_source": resolution.chosen_source,
        "compose_snippet": compose_snippet,
    }


# ═══════════════════════════════════════════════════════════
# STARTUP / SHUTDOWN
# ═══════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    logger.info("MapArr v1.0 starting up...")
    logger.info(f"Docker: {docker_manager.connection_method if docker_manager.is_connected else 'NOT CONNECTED'}")
    logger.info(f"Database: {db.db_path}")
    logger.info(f"Platform: {platform_mod.system()} {platform_mod.release()}")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("MapArr shutting down...")
    if docker_manager.client:
        docker_manager.client.close()


# ═══════════════════════════════════════════════════════════
# LOCAL DEVELOPMENT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9900, log_level="info")
