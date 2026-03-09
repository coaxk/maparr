"""Tests for CONFLICT_CATEGORIES mapping and Conflict.category property.

Verifies that every conflict type is mapped to a category letter (A-D),
and that the Conflict dataclass exposes the category via a property.
"""

import pytest

from backend.analyzer import CONFLICT_CATEGORIES, Conflict


# ─── Category mapping tests ───

class TestConflictCategories:
    """Test the CONFLICT_CATEGORIES constant dict."""

    CATEGORY_A_TYPES = [
        "no_shared_mount",
        "different_host_paths",
        "named_volume_data",
        "path_unreachable",
    ]

    CATEGORY_B_TYPES = [
        "puid_pgid_mismatch",
        "missing_puid_pgid",
        "root_execution",
        "umask_inconsistent",
        "umask_restrictive",
        "cross_stack_puid_mismatch",
        "tz_mismatch",
    ]

    CATEGORY_C_TYPES = [
        "wsl2_performance",
        "mixed_mount_types",
        "windows_path_in_compose",
        "remote_filesystem",
    ]

    CATEGORY_D_TYPES = [
        "missing_restart_policy",
        "latest_tag_usage",
        "missing_tz",
        "privileged_mode",
        "no_healthcheck",
    ]

    @pytest.mark.parametrize("conflict_type", CATEGORY_A_TYPES)
    def test_category_a_path_conflicts(self, conflict_type):
        assert CONFLICT_CATEGORIES[conflict_type] == "A"

    @pytest.mark.parametrize("conflict_type", CATEGORY_B_TYPES)
    def test_category_b_permission_env(self, conflict_type):
        assert CONFLICT_CATEGORIES[conflict_type] == "B"

    @pytest.mark.parametrize("conflict_type", CATEGORY_C_TYPES)
    def test_category_c_infrastructure(self, conflict_type):
        assert CONFLICT_CATEGORIES[conflict_type] == "C"

    @pytest.mark.parametrize("conflict_type", CATEGORY_D_TYPES)
    def test_category_d_observations(self, conflict_type):
        assert CONFLICT_CATEGORIES[conflict_type] == "D"

    def test_no_unknown_categories(self):
        """Every value in the dict must be A, B, C, or D."""
        valid = {"A", "B", "C", "D"}
        for conflict_type, category in CONFLICT_CATEGORIES.items():
            assert category in valid, f"{conflict_type} mapped to unknown category {category!r}"

    def test_all_expected_types_present(self):
        """Every type listed above must appear in the dict."""
        all_expected = (
            self.CATEGORY_A_TYPES
            + self.CATEGORY_B_TYPES
            + self.CATEGORY_C_TYPES
            + self.CATEGORY_D_TYPES
        )
        for t in all_expected:
            assert t in CONFLICT_CATEGORIES, f"Missing conflict type: {t}"


# ─── Conflict.category property tests ───

class TestConflictCategoryProperty:
    """Test the category property on the Conflict dataclass."""

    def _make_conflict(self, conflict_type: str) -> Conflict:
        return Conflict(
            conflict_type=conflict_type,
            severity="medium",
            services=["sonarr"],
            description="test",
        )

    def test_category_a(self):
        c = self._make_conflict("no_shared_mount")
        assert c.category == "A"

    def test_category_b(self):
        c = self._make_conflict("puid_pgid_mismatch")
        assert c.category == "B"

    def test_category_c(self):
        c = self._make_conflict("wsl2_performance")
        assert c.category == "C"

    def test_category_d(self):
        c = self._make_conflict("missing_restart_policy")
        assert c.category == "D"

    def test_unknown_type_returns_none(self):
        c = self._make_conflict("totally_made_up_type")
        assert c.category is None

    def test_category_in_to_dict(self):
        c = self._make_conflict("different_host_paths")
        d = c.to_dict()
        assert "category" in d
        assert d["category"] == "A"

    def test_category_none_in_to_dict(self):
        c = self._make_conflict("unknown_type")
        d = c.to_dict()
        assert "category" in d
        assert d["category"] is None
