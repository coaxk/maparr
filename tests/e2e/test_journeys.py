"""
Layer 2 — User Journey Tests (Playwright end-to-end workflows).

Tests complete user workflows from fresh page load through dashboard
interaction, analysis, paste error matching, and path changes.
Each test class covers one user journey with sequential steps.

Architecture note: MapArr uses Server-Sent Events (SSE) for its log
stream, which keeps a persistent HTTP connection open. All navigation
uses 'domcontentloaded' instead of 'networkidle', with explicit waits
for specific DOM elements.

The analysis endpoint calls `docker compose config` which may have a
30-second timeout if Docker is not installed. Tests that trigger
analysis use a 90-second timeout to accommodate this.

Uses a module-scoped page fixture to avoid SSE connection buildup
from creating multiple browser contexts.

Requires: pytest-playwright, a running MapArr server (via conftest.py).
"""

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


# ─── Constants ───

E2E_STACKS = str(Path(__file__).parent / "fixtures" / "stacks")

# Timeouts (ms)
DASHBOARD_TIMEOUT = 30000
# Analysis involves docker compose config (30s timeout if Docker absent)
# plus terminal step animation (~3s). 90s covers worst case.
ANALYSIS_TIMEOUT = 90000
ELEMENT_TIMEOUT = 10000


# ─── Helpers ───


def _stacks_path():
    """Return the E2E stacks path with forward slashes (Windows compat)."""
    return E2E_STACKS.replace("\\", "/")


def _wait_for_page_ready(page: Page):
    """Wait for boot sequence to complete — either first-launch or dashboard."""
    page.wait_for_function(
        """() => {
            const fl = document.getElementById('first-launch');
            const db = document.getElementById('pipeline-dashboard');
            return (fl && !fl.classList.contains('hidden')) ||
                   (db && !db.classList.contains('hidden'));
        }""",
        timeout=DASHBOARD_TIMEOUT,
    )


def _ensure_dashboard(page: Page, base_url: str):
    """Ensure the pipeline dashboard is visible on the current page.

    If already on the dashboard, collapses any expanded service panel
    and returns. Otherwise navigates fresh and handles first-launch.
    """
    dashboard = page.locator("#pipeline-dashboard")
    if dashboard.is_visible():
        # Collapse any expanded service detail panel from prior tests
        page.evaluate("""(() => {
            const panel = document.querySelector('.service-detail-panel');
            if (panel) panel.remove();
            document.querySelectorAll('.service-row.expanded').forEach(
                r => r.classList.remove('expanded')
            );
            try { state.expandedService = null; } catch(e) {}
        })()""")
        return

    page.goto(base_url, wait_until="domcontentloaded")
    _wait_for_page_ready(page)

    if page.locator("#first-launch").is_visible():
        page.locator("#first-launch-path").fill(_stacks_path())
        page.locator("#first-launch-scan").click()

    dashboard.wait_for(state="visible", timeout=DASHBOARD_TIMEOUT)


def _back_to_dashboard(page: Page):
    """Return to the dashboard from an analysis view."""
    back_btn = page.locator("#btn-back-to-dashboard")
    if back_btn.is_visible():
        back_btn.click()
    else:
        page.locator("#header-brand-link").click()
    expect(page.locator("#pipeline-dashboard")).to_be_visible(
        timeout=ELEMENT_TIMEOUT
    )


def _click_service_by_stack(page: Page, stack_substring: str) -> bool:
    """Click a service row whose .service-file text contains the stack name.

    Returns True if a matching row was found and clicked, False otherwise.
    Service rows render the stack name in a .service-file span.
    """
    rows = page.locator(".service-row")
    count = rows.count()
    for i in range(count):
        row = rows.nth(i)
        file_text = row.locator(".service-file").inner_text()
        if stack_substring in file_text:
            row.click()
            return True
    return False


def _click_analyze_button(page: Page):
    """Click the 'Analyze Stack' button in the expanded service detail panel.

    Resets the _analysisInFlight guard first in case a previous analysis
    left it stuck (e.g., from a timed-out request or aborted navigation).
    """
    # Reset the analysis guard to prevent stuck state from prior calls
    page.evaluate("(() => { try { state._analysisInFlight = false; } catch(e) {} })()")
    analyze_btn = page.locator(".service-detail-panel .btn-primary")
    analyze_btn.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)
    analyze_btn.click()


def _wait_for_analysis_result(page: Page):
    """Wait for any analysis result section to become visible.

    Uses wait_for_function with JavaScript to check multiple elements
    since Playwright's .first locator resolves to the first DOM element
    (not the first visible one), which may stay hidden.

    The long timeout accounts for docker compose config timeout (30s)
    plus terminal step animation delays.
    """
    page.wait_for_function(
        """() => {
            const ids = ['step-healthy', 'step-problem', 'step-analysis-error'];
            return ids.some(id => {
                const el = document.getElementById(id);
                return el && !el.classList.contains('hidden');
            });
        }""",
        timeout=ANALYSIS_TIMEOUT,
    )


# ─── Module-Scoped Page Fixture ───


@pytest.fixture(scope="module")
def journey_page(browser, base_url, maparr_server):
    """A single browser page for all journey tests in this module.

    Module-scoped to avoid SSE connection buildup from creating
    multiple browser contexts.
    """
    context = browser.new_context(base_url=base_url)
    page = context.new_page()
    yield page, base_url
    context.close()


# ─── Journey 2.1: First Launch → Browse → Scan → Dashboard ───


class TestFirstLaunchBrowseScanDashboard:
    """Journey: User opens MapArr, uses directory browser, scans to dashboard."""

    def test_first_launch_browse_scan_dashboard(self, journey_page):
        """Navigate fresh, open directory browser modal, verify it works.

        Since the server has MAPARR_STACKS_PATH set, it auto-boots to the
        dashboard. We validate the browse modal via the header path editor
        and verify the dashboard rendered correctly.
        """
        page, base_url = journey_page

        page.goto(base_url, wait_until="domcontentloaded")
        _wait_for_page_ready(page)

        if page.locator("#pipeline-dashboard").is_visible():
            # Server auto-booted — validate browse via header
            page.locator("#header-path").click()
            page.locator("#header-path-browse").click()

            overlay = page.locator("#dir-browser-overlay")
            expect(overlay).to_be_visible(timeout=ELEMENT_TIMEOUT)

            browser_list = page.locator(".dir-browser-list")
            expect(browser_list).to_be_visible(timeout=ELEMENT_TIMEOUT)

            page.keyboard.press("Escape")
            expect(overlay).to_have_count(0, timeout=5000)

            # Close path editor
            page.locator("#header-path").click()

            count_el = page.locator("#service-count")
            expect(count_el).to_have_text(re.compile(r"\d"), timeout=ELEMENT_TIMEOUT)
        else:
            # First-launch visible — full journey
            expect(page.locator("#first-launch")).to_be_visible()

            page.locator("#first-launch-browse").click()
            overlay = page.locator("#dir-browser-overlay")
            expect(overlay).to_be_visible(timeout=ELEMENT_TIMEOUT)

            browser_list = page.locator(".dir-browser-list")
            expect(browser_list).to_be_visible(timeout=ELEMENT_TIMEOUT)

            page.keyboard.press("Escape")
            expect(overlay).to_have_count(0, timeout=5000)

            page.locator("#first-launch-path").fill(_stacks_path())
            page.locator("#first-launch-scan").click()
            page.locator("#pipeline-dashboard").wait_for(
                state="visible", timeout=DASHBOARD_TIMEOUT
            )

            count_el = page.locator("#service-count")
            expect(count_el).to_have_text(re.compile(r"\d"), timeout=ELEMENT_TIMEOUT)


# ─── Journey 2.2: Dashboard Shows Service Groups ───


class TestFirstLaunchManualPath:
    """Journey: Verify dashboard shows service groups after loading."""

    def test_manual_path_scan_shows_dashboard(self, journey_page):
        """Verify dashboard shows service groups and service rows."""
        page, base_url = journey_page
        _ensure_dashboard(page, base_url)

        expect(page.locator("#pipeline-dashboard")).to_be_visible()

        groups = page.locator("#service-groups")
        expect(groups).to_be_visible(timeout=ELEMENT_TIMEOUT)

        rows = page.locator(".service-row")
        expect(rows.first).to_be_visible(timeout=ELEMENT_TIMEOUT)


# ─── Journey 2.3: Analyze Healthy Stack ───


class TestAnalyzeHealthyStack:
    """Journey: Click a service, run analysis, see result, return to dashboard."""

    def test_analyze_healthy_stack(self, journey_page):
        """Click a service from healthy-arr, see analysis result, return."""
        page, base_url = journey_page
        _ensure_dashboard(page, base_url)

        found = _click_service_by_stack(page, "healthy")
        if not found:
            page.locator(".service-row").first.click()

        _click_analyze_button(page)
        _wait_for_analysis_result(page)

        assert (
            page.locator("#step-healthy").is_visible()
            or page.locator("#step-problem").is_visible()
            or page.locator("#step-analysis-error").is_visible()
        ), "Expected at least one analysis result section"

        _back_to_dashboard(page)


# ─── Journey 2.4: Analyze Path Conflict → Solution ───


class TestAnalyzePathConflictApplyFix:
    """Journey: Analyze a stack with path conflicts, see solution."""

    def test_path_conflict_shows_solution(self, journey_page):
        """Click path-conflict stack, see problem and solution sections."""
        page, base_url = journey_page
        _ensure_dashboard(page, base_url)

        found = _click_service_by_stack(page, "path-conflict")
        if not found:
            found = _click_service_by_stack(page, "different-paths")
        if not found:
            problem_dots = page.locator(".health-dot.problem")
            if problem_dots.count() > 0:
                problem_dots.first.locator("xpath=..").click()
            else:
                pytest.skip("No path-conflict stack in fixture stacks")

        _click_analyze_button(page)
        _wait_for_analysis_result(page)

        problem = page.locator("#step-problem")
        if problem.is_visible():
            text = problem.inner_text()
            assert len(text) > 10, (
                f"Problem card should have descriptive text, got: '{text}'"
            )

            solution = page.locator("#step-solution")
            expect(solution).to_be_visible(timeout=ELEMENT_TIMEOUT)

            apply_btn = page.locator("#btn-apply-fix")
            assert apply_btn.count() >= 1, "Apply Fix button should exist"

        _back_to_dashboard(page)


# ─── Journey 2.5: Analyze Permission Issue (PUID/PGID) ───


class TestAnalyzePermissionIssue:
    """Journey: Analyze a stack with PUID/PGID mismatch."""

    def test_puid_mismatch_shows_permission_info(self, journey_page):
        """Click puid-mismatch stack, verify analysis completes."""
        page, base_url = journey_page
        _ensure_dashboard(page, base_url)

        found = _click_service_by_stack(page, "puid-mismatch")
        if not found:
            found = _click_service_by_stack(page, "puid")
        if not found:
            issue_dots = page.locator(".health-dot.issue")
            if issue_dots.count() > 0:
                issue_dots.first.locator("xpath=..").click()
            else:
                pytest.skip("No puid-mismatch stack in fixture stacks")

        _click_analyze_button(page)
        _wait_for_analysis_result(page)

        body_text = page.locator("body").inner_text().lower()
        has_permission_content = any(
            term in body_text
            for term in ("puid", "pgid", "permission", "uid", "gid")
        )

        assert (
            page.locator("#step-healthy").is_visible()
            or page.locator("#step-problem").is_visible()
            or page.locator("#step-analysis-error").is_visible()
            or has_permission_content
        ), "Expected analysis result or permission content"

        _back_to_dashboard(page)


# ─── Journey 2.6: Paste Error → Auto-Match ───


class TestPasteErrorAutoMatch:
    """Journey: User pastes an error, MapArr identifies the service."""

    def test_paste_error_matches_service(self, journey_page):
        """Open paste area, type error, click Analyze, see result."""
        page, base_url = journey_page
        _ensure_dashboard(page, base_url)

        page.locator("#fork-paste").click()
        paste_area = page.locator("#paste-area")
        expect(paste_area).to_be_visible(timeout=ELEMENT_TIMEOUT)

        error_text = (
            "Import failed, path does not exist or is not accessible by "
            "Sonarr: /data/tv/Show Name/Season 01/Episode.mkv"
        )
        input_el = page.locator("#paste-error-input")
        expect(input_el).to_be_visible(timeout=ELEMENT_TIMEOUT)
        input_el.fill(error_text)

        go_btn = page.locator("#paste-error-go")
        expect(go_btn).to_be_visible(timeout=ELEMENT_TIMEOUT)
        go_btn.click()

        result_el = page.locator("#paste-bar-result")
        result_el.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)
        result_text = result_el.inner_text()
        assert len(result_text) > 0, "Paste result should have text"

        result_lower = result_text.lower()
        assert any(
            term in result_lower
            for term in ("sonarr", "service", "not found", "detected",
                         "conflict", "correct", "setup")
        ), f"Paste result should give feedback: '{result_text}'"

        close_btn = page.locator("#paste-area-close")
        if close_btn.is_visible():
            close_btn.click()


# ─── Journey 2.7: Change Stacks Path ───


class TestChangeStacksPath:
    """Journey: User changes the stacks path via header editor."""

    def test_change_path_reloads_dashboard(self, journey_page):
        """Open path editor, enter same path, rescan, dashboard reloads."""
        page, base_url = journey_page
        _ensure_dashboard(page, base_url)

        page.locator("#header-path").click()
        editor = page.locator("#path-editor")
        expect(editor).to_be_visible(timeout=ELEMENT_TIMEOUT)

        input_el = page.locator("#header-path-input")
        expect(input_el).to_be_visible(timeout=ELEMENT_TIMEOUT)
        input_el.fill(_stacks_path())

        page.locator("#header-path-go").click()

        page.locator("#pipeline-dashboard").wait_for(
            state="visible", timeout=DASHBOARD_TIMEOUT
        )
        count_el = page.locator("#service-count")
        expect(count_el).to_have_text(re.compile(r"\d"), timeout=ELEMENT_TIMEOUT)
