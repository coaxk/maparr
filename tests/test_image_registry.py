"""
Tests for MapArr Image DB — ImageRegistry.

Covers: loading, image classification, family lookup, keyword sets,
custom overrides, error handling, multi-instance support.
"""

import json
import os

import pytest


# Minimal valid DB for testing (avoids depending on real data/images.json)
MINIMAL_DB = {
    "version": 1,
    "generated_at": "2026-01-01T00:00:00Z",
    "families": {
        "linuxserver": {
            "name": "LinuxServer.io",
            "uid_env": "PUID",
            "gid_env": "PGID",
            "umask_env": "UMASK",
            "default_uid": "911",
            "default_gid": "911",
            "needs_puid": True,
        },
        "jlesage": {
            "name": "jlesage",
            "uid_env": "USER_ID",
            "gid_env": "GROUP_ID",
            "umask_env": "UMASK",
            "default_uid": "1000",
            "default_gid": "1000",
            "needs_puid": True,
        },
    },
    "images": {
        "sonarr": {
            "name": "Sonarr",
            "role": "arr",
            "family": "linuxserver",
            "patterns": ["lscr.io/linuxserver/sonarr", "linuxserver/sonarr"],
            "keywords": ["sonarr"],
            "hardlink_capable": True,
            "docs_url": "https://docs.linuxserver.io/images/docker-sonarr",
        },
        "qbittorrent": {
            "name": "qBittorrent",
            "role": "download_client",
            "family": "linuxserver",
            "patterns": ["lscr.io/linuxserver/qbittorrent"],
            "keywords": ["qbittorrent", "qbit"],
            "hardlink_capable": True,
            "docs_url": None,
        },
        "plex": {
            "name": "Plex",
            "role": "media_server",
            "family": None,
            "patterns": ["plexinc/pms-docker"],
            "keywords": ["plex"],
            "hardlink_capable": True,
            "docs_url": "https://support.plex.tv/",
        },
        "jdownloader": {
            "name": "JDownloader",
            "role": "download_client",
            "family": "jlesage",
            "patterns": ["jlesage/jdownloader-2"],
            "keywords": ["jdownloader", "jdown", "jd2"],
            "hardlink_capable": True,
            "docs_url": None,
        },
        "prowlarr": {
            "name": "Prowlarr",
            "role": "arr",
            "family": "linuxserver",
            "patterns": ["lscr.io/linuxserver/prowlarr"],
            "keywords": ["prowlarr"],
            "hardlink_capable": False,
            "docs_url": None,
        },
        "overseerr": {
            "name": "Overseerr",
            "role": "request",
            "family": None,
            "patterns": ["sctx/overseerr"],
            "keywords": ["overseerr"],
            "hardlink_capable": False,
            "docs_url": None,
        },
    },
}


@pytest.fixture
def db_dir(tmp_path):
    """Create a temp data dir with the minimal DB written to images.json."""
    db_file = tmp_path / "images.json"
    db_file.write_text(json.dumps(MINIMAL_DB), encoding="utf-8")
    return tmp_path


@pytest.fixture
def registry(db_dir):
    """Return a loaded ImageRegistry from the minimal DB."""
    from backend.image_registry import ImageRegistry
    reg = ImageRegistry()
    reg.load(db_dir)
    return reg


# ─── Loading ───

class TestLoading:
    def test_load_counts(self, registry):
        assert registry.image_count >= 6
        assert registry.family_count >= 2

    def test_load_missing_file_uses_fallback(self, tmp_path):
        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(tmp_path / "nonexistent")
        # Should fall back to hardcoded safety net
        result = reg.classify("sonarr", "linuxserver/sonarr")
        assert result["role"] == "arr"

    def test_load_malformed_json(self, tmp_path):
        bad = tmp_path / "images.json"
        bad.write_text("NOT JSON {{{", encoding="utf-8")
        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(tmp_path)
        # Should fall back to safety net
        assert reg.image_count > 0


# ─── Image Classification ───

class TestClassify:
    def test_classify_by_image_pattern(self, registry):
        result = registry.classify("my-sonarr", "lscr.io/linuxserver/sonarr:latest")
        assert result["role"] == "arr"
        assert result["name"] == "Sonarr"
        assert result["hardlink_capable"] is True

    def test_classify_by_keyword_fallback(self, registry):
        result = registry.classify("sonarr", "some-custom-registry/sonarr-fork:v4")
        assert result["role"] == "arr"

    def test_classify_case_insensitive(self, registry):
        result = registry.classify("SONARR", "LSCR.IO/LINUXSERVER/SONARR:LATEST")
        assert result["role"] == "arr"

    def test_classify_unknown_image(self, registry):
        result = registry.classify("watchtower", "containrrr/watchtower:latest")
        assert result["role"] == "other"
        assert result["family_name"] is None
        assert result["hardlink_capable"] is False

    def test_classify_download_client(self, registry):
        result = registry.classify("qbit", "lscr.io/linuxserver/qbittorrent:latest")
        assert result["role"] == "download_client"

    def test_classify_media_server(self, registry):
        result = registry.classify("plex", "plexinc/pms-docker:latest")
        assert result["role"] == "media_server"

    def test_classify_request_app(self, registry):
        result = registry.classify("overseerr", "sctx/overseerr:latest")
        assert result["role"] == "request"

    def test_classify_jlesage_family(self, registry):
        result = registry.classify("jd2", "jlesage/jdownloader-2:latest")
        assert result["role"] == "download_client"
        assert result["family_name"] == "jlesage"

    def test_classify_abbreviation_keyword(self, registry):
        """Keywords like 'qbit' should match qbittorrent."""
        result = registry.classify("qbit-downloads", "custom-image:latest")
        assert result["role"] == "download_client"

    def test_classify_returns_docs_url(self, registry):
        result = registry.classify("sonarr", "lscr.io/linuxserver/sonarr:latest")
        assert result["docs_url"] == "https://docs.linuxserver.io/images/docker-sonarr"

    def test_classify_null_docs_url(self, registry):
        result = registry.classify("qbit", "lscr.io/linuxserver/qbittorrent:latest")
        assert result["docs_url"] is None

    def test_multi_instance_same_image(self, registry):
        """Two services using the same image both classify correctly."""
        r1 = registry.classify("sonarr-anime", "lscr.io/linuxserver/sonarr:latest")
        r2 = registry.classify("sonarr-tv", "lscr.io/linuxserver/sonarr:latest")
        assert r1["role"] == "arr"
        assert r2["role"] == "arr"


# ─── Family Lookup ───

class TestFamilyLookup:
    def test_get_family_by_image(self, registry):
        family = registry.get_family("lscr.io/linuxserver/sonarr:latest")
        assert family is not None
        assert family["name"] == "LinuxServer.io"
        assert family["uid_env"] == "PUID"

    def test_get_family_independent(self, registry):
        family = registry.get_family("plexinc/pms-docker:latest")
        # Plex has family=None in our test data
        assert family is None

    def test_get_family_unknown_image(self, registry):
        family = registry.get_family("containrrr/watchtower:latest")
        assert family is None

    def test_get_family_jlesage(self, registry):
        family = registry.get_family("jlesage/jdownloader-2:latest")
        assert family is not None
        assert family["uid_env"] == "USER_ID"
        assert family["gid_env"] == "GROUP_ID"


# ─── Keyword Sets ───

class TestKeywordSets:
    def test_known_keywords_complete(self, registry):
        kw = registry.known_keywords()
        assert "sonarr" in kw
        assert "qbittorrent" in kw
        assert "plex" in kw
        assert "jdownloader" in kw
        # Abbreviations too
        assert "qbit" in kw
        assert "jd2" in kw

    def test_known_by_role_arr(self, registry):
        arr = registry.known_by_role("arr")
        assert "sonarr" in arr
        assert "prowlarr" in arr
        assert "qbittorrent" not in arr

    def test_known_by_role_download(self, registry):
        dl = registry.known_by_role("download_client")
        assert "qbittorrent" in dl
        assert "sonarr" not in dl

    def test_known_by_role_nonexistent(self, registry):
        result = registry.known_by_role("nonexistent_role")
        assert result == set()

    def test_hardlink_participants(self, registry):
        participants = registry.hardlink_participants()
        assert "sonarr" in participants
        assert "qbittorrent" in participants
        assert "plex" in participants
        # prowlarr is NOT a hardlink participant
        assert "prowlarr" not in participants


# ─── Custom Overrides ───

class TestCustomOverrides:
    def test_custom_override_replaces_entry(self, db_dir):
        custom = {
            "version": 1,
            "families": {},
            "images": {
                "sonarr": {
                    "name": "Sonarr Custom",
                    "role": "arr",
                    "family": "linuxserver",
                    "patterns": ["my-registry/sonarr"],
                    "keywords": ["sonarr"],
                    "hardlink_capable": True,
                    "docs_url": "https://custom.example.com",
                },
            },
        }
        (db_dir / "custom-images.json").write_text(json.dumps(custom), encoding="utf-8")

        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)

        result = reg.classify("sonarr", "my-registry/sonarr:latest")
        assert result["name"] == "Sonarr Custom"
        assert result["docs_url"] == "https://custom.example.com"

    def test_custom_adds_new_entry(self, db_dir):
        custom = {
            "version": 1,
            "families": {},
            "images": {
                "myapp": {
                    "name": "My Custom App",
                    "role": "arr",
                    "family": None,
                    "patterns": ["myregistry/myapp"],
                    "keywords": ["myapp"],
                    "hardlink_capable": True,
                    "docs_url": None,
                },
            },
        }
        (db_dir / "custom-images.json").write_text(json.dumps(custom), encoding="utf-8")

        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)

        result = reg.classify("myapp", "myregistry/myapp:latest")
        assert result["role"] == "arr"
        assert result["name"] == "My Custom App"

    def test_custom_adds_new_family(self, db_dir):
        custom = {
            "version": 1,
            "families": {
                "custom_family": {
                    "name": "Custom Family",
                    "uid_env": "MY_UID",
                    "gid_env": "MY_GID",
                    "umask_env": None,
                    "default_uid": "500",
                    "default_gid": "500",
                    "needs_puid": True,
                },
            },
            "images": {
                "myapp": {
                    "name": "My App",
                    "role": "arr",
                    "family": "custom_family",
                    "patterns": ["myregistry/myapp"],
                    "keywords": ["myapp"],
                    "hardlink_capable": True,
                    "docs_url": None,
                },
            },
        }
        (db_dir / "custom-images.json").write_text(json.dumps(custom), encoding="utf-8")

        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)

        family = reg.get_family("myregistry/myapp:latest")
        assert family is not None
        assert family["uid_env"] == "MY_UID"

    def test_malformed_custom_file_skipped(self, db_dir):
        (db_dir / "custom-images.json").write_text("NOT JSON", encoding="utf-8")

        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)

        # Should still work with baked-in data
        result = reg.classify("sonarr", "lscr.io/linuxserver/sonarr:latest")
        assert result["role"] == "arr"

    def test_missing_custom_file_silent(self, db_dir):
        """No custom-images.json should not log errors or fail."""
        from backend.image_registry import ImageRegistry
        reg = ImageRegistry()
        reg.load(db_dir)
        assert reg.image_count >= 6


# ─── Display Labels ───

class TestDisplayLabels:
    def test_family_name_for_known(self, registry):
        result = registry.classify("sonarr", "lscr.io/linuxserver/sonarr:latest")
        assert result["family_name"] == "LinuxServer.io"

    def test_family_name_independent(self, registry):
        result = registry.classify("watchtower", "containrrr/watchtower:latest")
        assert result["family_name"] is None
        # Display code should render None as "Independent"
