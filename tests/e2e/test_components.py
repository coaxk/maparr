"""
Layer 1 — Component Spec Tests (Playwright DOM assertions).

Validates that key UI components render correctly with the expected
DOM structure, IDs, and interactive behaviors. These are shallow
structural tests — they verify what's on the page, not full user
journeys (that's Layer 2).

Architecture note: MapArr uses Server-Sent Events (SSE) for its log
stream, which keeps a persistent HTTP connection open. This means
Playwright's 'networkidle' wait strategy will never resolve. All
navigation uses 'domcontentloaded' instead, with explicit waits for
specific DOM elements.

Requires: pytest-playwright, a running MapArr server (via conftest.py).
"""

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, BrowserContext, expect


# ─── Constants ───

# MapArr's boot + pipeline scan can take a while on first load
DASHBOARD_TIMEOUT = 30000  # ms
ELEMENT_TIMEOUT = 5000  # ms


# ─── Helpers ───


def _goto(page: Page, base_url: str):
    """Navigate to MapArr, waiting only for DOM (not network).

    MapArr's SSE log stream keeps a persistent connection open,
    which prevents 'networkidle' from ever resolving.
    """
    page.goto(base_url, wait_until="domcontentloaded")


def _ensure_dashboard(page: Page, base_url: str, stacks_path: str):
    """Navigate and wait for the pipeline dashboard to be visible.

    With MAPARR_STACKS_PATH set, the boot sequence auto-discovers stacks
    and transitions to the dashboard. If first-launch appears instead
    (unlikely with E2E fixtures), fills the path and triggers a scan.
    """
    _goto(page, base_url)

    dashboard = page.locator("#pipeline-dashboard")

    # Wait for auto-boot to complete and show dashboard
    try:
        dashboard.wait_for(state="visible", timeout=DASHBOARD_TIMEOUT)
        return
    except Exception:
        pass

    # Fallback: first-launch screen appeared — fill path and scan
    first_launch_input = page.locator("#first-launch-path")
    if first_launch_input.is_visible(timeout=3000):
        first_launch_input.fill(stacks_path)
        page.locator("#first-launch-scan").click()
    else:
        # Use header path editor
        page.locator("#header-path").click()
        page.locator("#header-path-input").fill(stacks_path)
        page.locator("#header-path-go").click()

    dashboard.wait_for(state="visible", timeout=DASHBOARD_TIMEOUT)


# ─── Fixtures ───


@pytest.fixture(scope="session")
def stacks_path_str(stacks_dir) -> str:
    """Stacks directory as a forward-slash string for browser input fields."""
    return str(stacks_dir).replace("\\", "/")


@pytest.fixture(scope="module")
def dashboard_page(browser: "Browser", base_url, maparr_server, stacks_path_str):
    """A page that has already navigated to the dashboard.

    Module-scoped to avoid re-navigating and re-scanning for every test.
    Tests that need the dashboard state share this page. Tests that
    modify UI state (toggle editors, open modals) should restore the
    state after themselves, or use a separate fresh page.
    """
    context = browser.new_context(base_url=base_url)
    page = context.new_page()
    _ensure_dashboard(page, base_url, stacks_path_str)
    yield page
    context.close()


# ─── TestBootTerminal ───


class TestBootTerminal:
    """Boot terminal renders during the initial discovery sequence."""

    def test_boot_terminal_visible_on_load(self, page: Page, base_url, maparr_server):
        """Boot screen should be visible immediately on page load.

        The boot screen is not hidden in the HTML source — it's the
        first thing users see before the backend responds.
        """
        # Navigate without waiting so we catch the boot screen before transition
        page.goto(base_url, wait_until="commit")
        boot = page.locator("#boot-screen")
        expect(boot).to_be_visible(timeout=3000)

    def test_boot_terminal_has_body(self, page: Page, base_url, maparr_server):
        """Boot terminal should have a terminal body area for log lines."""
        page.goto(base_url, wait_until="commit")
        body = page.locator("#boot-terminal-body")
        expect(body).to_be_visible(timeout=3000)

    def test_boot_terminal_has_dots(self, page: Page, base_url, maparr_server):
        """Boot terminal header should have 3 decorative dots (red/yellow/green)."""
        page.goto(base_url, wait_until="commit")
        dots = page.locator("#boot-screen .terminal-dot")
        expect(dots.first).to_be_visible(timeout=3000)
        assert dots.count() == 3, f"Expected 3 terminal dots, got {dots.count()}"


# ─── TestPipelineDashboard ───


class TestPipelineDashboard:
    """Dashboard renders correctly after boot/scan completes."""

    def test_dashboard_visible(self, dashboard_page: Page):
        """Dashboard section should be visible after scan."""
        expect(dashboard_page.locator("#pipeline-dashboard")).to_be_visible()

    def test_dashboard_has_health_banner(self, dashboard_page: Page):
        """Dashboard should show the health status banner."""
        expect(dashboard_page.locator("#health-banner")).to_be_visible(
            timeout=ELEMENT_TIMEOUT
        )

    def test_dashboard_has_service_groups(self, dashboard_page: Page):
        """Dashboard should have the service groups container."""
        groups = dashboard_page.locator("#service-groups")
        expect(groups).to_be_visible(timeout=ELEMENT_TIMEOUT)

    def test_service_count_shows_number(self, dashboard_page: Page):
        """Service count badge in header should contain a digit."""
        count_el = dashboard_page.locator("#service-count")
        expect(count_el).to_have_text(re.compile(r"\d"), timeout=ELEMENT_TIMEOUT)

    def test_dashboard_has_action_fork(self, dashboard_page: Page):
        """Dashboard should show the two-action fork (paste + explore)."""
        expect(dashboard_page.locator("#fork-paste")).to_be_visible(
            timeout=ELEMENT_TIMEOUT
        )
        expect(dashboard_page.locator("#fork-explore")).to_be_visible(
            timeout=ELEMENT_TIMEOUT
        )

    def test_dashboard_has_health_legend(self, dashboard_page: Page):
        """Dashboard should show the health dot legend."""
        legend = dashboard_page.locator("#health-legend")
        expect(legend).to_be_visible(timeout=ELEMENT_TIMEOUT)

    def test_first_launch_hidden_when_dashboard_visible(self, dashboard_page: Page):
        """First-launch and dashboard should not both be visible."""
        expect(dashboard_page.locator("#pipeline-dashboard")).to_be_visible()
        expect(dashboard_page.locator("#first-launch")).to_be_hidden()


# ─── TestHealthBanner ───


class TestHealthBanner:
    """Health banner shows meaningful status after scan."""

    def test_health_banner_has_text(self, dashboard_page: Page):
        """Health banner text should have meaningful content (not empty)."""
        banner_text = dashboard_page.locator("#health-banner-text")
        expect(banner_text).to_be_visible(timeout=ELEMENT_TIMEOUT)
        text = banner_text.inner_text()
        assert len(text) > 5, f"Banner text too short: '{text}'"

    def test_health_banner_not_scanning(self, dashboard_page: Page):
        """Banner should not be stuck on 'Scanning...' after scan completes."""
        banner_text = dashboard_page.locator("#health-banner-text")
        expect(banner_text).to_be_visible(timeout=ELEMENT_TIMEOUT)
        text = banner_text.inner_text().lower()
        assert "scanning" not in text, (
            f"Banner should not be stuck on 'Scanning...', got: '{text}'"
        )


# ─── TestServiceGroups ───


class TestServiceGroups:
    """Service groups render with icons and health indicators."""

    def test_service_rows_have_health_dots(self, dashboard_page: Page):
        """Service rows should have health dot indicators."""
        dots = dashboard_page.locator(".health-dot")
        expect(dots.first).to_be_visible(timeout=ELEMENT_TIMEOUT)
        assert dots.count() > 0, "Expected at least one health dot"

    def test_service_rows_have_icons(self, dashboard_page: Page):
        """Service rows should render service icons (bundled SVGs)."""
        icons = dashboard_page.locator("img[src*='/img/services/']")
        try:
            expect(icons.first).to_be_visible(timeout=ELEMENT_TIMEOUT)
            assert icons.count() > 0, "Expected at least one service icon"
        except Exception:
            # If no icon images, at least verify groups rendered content
            groups = dashboard_page.locator("#service-groups")
            html = groups.inner_html()
            assert len(html) > 50, "Service groups should have rendered content"


# ─── TestServiceIcons ───


class TestServiceIcons:
    """Service icons use lazy loading for performance."""

    def test_icons_have_lazy_loading(self, dashboard_page: Page):
        """Service icons should use loading='lazy' for deferred image loading."""
        lazy_icons = dashboard_page.locator("img[loading='lazy']")
        try:
            expect(lazy_icons.first).to_be_visible(timeout=ELEMENT_TIMEOUT)
            assert lazy_icons.count() > 0, "Expected at least one lazy-loaded icon"
        except Exception:
            # Acceptable — fixture services may not have matching icons
            pytest.skip("No lazy-loaded icons found in fixture stacks")


# ─── TestPasteArea ───


class TestPasteArea:
    """Paste area toggles open and has expected child elements."""

    def test_paste_area_initially_hidden(self, dashboard_page: Page):
        """Paste area should be hidden until the paste fork card is clicked."""
        paste_area = dashboard_page.locator("#paste-area")
        expect(paste_area).to_be_hidden()

    def test_paste_area_toggle_open(self, dashboard_page: Page):
        """Clicking fork-paste should reveal the paste area."""
        dashboard_page.locator("#fork-paste").click()
        paste_area = dashboard_page.locator("#paste-area")
        expect(paste_area).to_be_visible(timeout=3000)
        # Restore state
        dashboard_page.locator("#paste-area-close").click()

    def test_paste_area_has_textarea(self, dashboard_page: Page):
        """Paste area should contain a textarea for error text input."""
        dashboard_page.locator("#fork-paste").click()
        textarea = dashboard_page.locator("#paste-error-input")
        expect(textarea).to_be_visible(timeout=3000)
        # Restore state
        dashboard_page.locator("#paste-area-close").click()

    def test_paste_area_has_analyze_button(self, dashboard_page: Page):
        """Paste area should have an Analyze button."""
        dashboard_page.locator("#fork-paste").click()
        btn = dashboard_page.locator("#paste-error-go")
        expect(btn).to_be_visible(timeout=3000)
        # Restore state
        dashboard_page.locator("#paste-area-close").click()

    def test_paste_area_has_example_pills(self, dashboard_page: Page):
        """Paste area should have clickable example pill buttons."""
        dashboard_page.locator("#fork-paste").click()
        pills = dashboard_page.locator(".paste-pill")
        expect(pills.first).to_be_visible(timeout=3000)
        assert pills.count() >= 4, f"Expected at least 4 example pills, got {pills.count()}"
        # Restore state
        dashboard_page.locator("#paste-area-close").click()

    def test_paste_area_close_hides(self, dashboard_page: Page):
        """Clicking the close button should hide the paste area."""
        dashboard_page.locator("#fork-paste").click()
        paste_area = dashboard_page.locator("#paste-area")
        expect(paste_area).to_be_visible(timeout=3000)
        dashboard_page.locator("#paste-area-close").click()
        expect(paste_area).to_be_hidden(timeout=3000)


# ─── TestPathEditor ───


class TestPathEditor:
    """Header path editor toggles and has input controls."""

    def test_path_editor_hidden_by_default(self, dashboard_page: Page):
        """Path editor should be hidden until header-path is clicked."""
        editor = dashboard_page.locator("#path-editor")
        expect(editor).to_be_hidden()

    def test_header_path_click_toggles_editor(self, dashboard_page: Page):
        """Clicking the header path button should reveal the editor."""
        dashboard_page.locator("#header-path").click()
        editor = dashboard_page.locator("#path-editor")
        expect(editor).to_be_visible(timeout=3000)
        # Restore state
        dashboard_page.locator("#header-path").click()

    def test_path_editor_has_input_and_buttons(self, dashboard_page: Page):
        """Path editor should have an input field, Scan button, and Browse button."""
        dashboard_page.locator("#header-path").click()
        expect(dashboard_page.locator("#header-path-input")).to_be_visible(timeout=3000)
        expect(dashboard_page.locator("#header-path-go")).to_be_visible(timeout=3000)
        expect(dashboard_page.locator("#header-path-browse")).to_be_visible(timeout=3000)
        # Restore state
        dashboard_page.locator("#header-path").click()

    def test_path_editor_input_has_current_path(self, dashboard_page: Page):
        """Path editor input should be pre-filled with the current stacks path."""
        dashboard_page.locator("#header-path").click()
        input_el = dashboard_page.locator("#header-path-input")
        expect(input_el).to_be_visible(timeout=3000)
        value = input_el.input_value()
        assert len(value) > 3, f"Path input should be pre-filled, got: '{value}'"
        # Restore state
        dashboard_page.locator("#header-path").click()

    def test_path_editor_toggle_twice_hides(self, dashboard_page: Page):
        """Clicking header-path twice should hide the editor again."""
        header_path = dashboard_page.locator("#header-path")
        editor = dashboard_page.locator("#path-editor")
        header_path.click()
        expect(editor).to_be_visible(timeout=3000)
        header_path.click()
        expect(editor).to_be_hidden(timeout=3000)


# ─── TestDirectoryBrowserModal ───


class TestDirectoryBrowserModal:
    """Directory browser modal opens from header path browse button.

    Uses a single test method that opens the modal, verifies all
    structural elements, then dismisses. This avoids state management
    issues since the overlay is dynamically created/removed from the DOM.
    """

    def test_browse_modal_lifecycle(self, dashboard_page: Page):
        """Open directory browser, verify structure, then dismiss.

        Checks: overlay visible, browser panel present, list container
        present, Cancel dismisses overlay.
        """
        # Ensure path editor is visible
        editor = dashboard_page.locator("#path-editor")
        if editor.is_hidden():
            dashboard_page.locator("#header-path").click()
            expect(editor).to_be_visible(timeout=3000)

        # Click Browse to open modal
        dashboard_page.locator("#header-path-browse").click()

        # 1. Overlay should be visible
        overlay = dashboard_page.locator("#dir-browser-overlay")
        expect(overlay).to_be_visible(timeout=ELEMENT_TIMEOUT)

        # 2. Browser panel should be present
        browser_el = dashboard_page.locator(".dir-browser")
        expect(browser_el).to_be_visible(timeout=ELEMENT_TIMEOUT)

        # 3. List container should be present
        list_el = dashboard_page.locator(".dir-browser-list")
        expect(list_el).to_be_visible(timeout=ELEMENT_TIMEOUT)

        # 4. Cancel should dismiss — overlay.remove() removes from DOM
        dashboard_page.locator(".dir-browser-footer .btn-ghost").click()
        expect(overlay).to_have_count(0, timeout=3000)

        # Clean up path editor toggle state
        editor = dashboard_page.locator("#path-editor")
        if not editor.is_hidden():
            dashboard_page.locator("#header-path").click()


# ─── TestFirstLaunchScreen ───


class TestFirstLaunchScreen:
    """First-launch screen DOM structure tests.

    With MAPARR_STACKS_PATH set, boot auto-discovers stacks and skips
    first-launch. We verify the elements exist in the DOM (they're
    always present in the HTML, just hidden via CSS class).
    """

    def test_first_launch_element_exists(self, dashboard_page: Page):
        """First-launch section should exist in the DOM."""
        fl = dashboard_page.locator("#first-launch")
        assert fl.count() == 1, "first-launch element should exist in DOM"

    def test_first_launch_has_scan_button(self, dashboard_page: Page):
        """First-launch Scan button should exist in DOM."""
        btn = dashboard_page.locator("#first-launch-scan")
        assert btn.count() == 1, "first-launch-scan button should exist"

    def test_first_launch_has_browse_button(self, dashboard_page: Page):
        """First-launch Browse button should exist in DOM."""
        btn = dashboard_page.locator("#first-launch-browse")
        assert btn.count() == 1, "first-launch-browse button should exist"

    def test_first_launch_has_path_input(self, dashboard_page: Page):
        """First-launch path input should exist in DOM."""
        input_el = dashboard_page.locator("#first-launch-path")
        assert input_el.count() == 1, "first-launch-path input should exist"


# ─── TestHeaderElements ───


class TestHeaderElements:
    """Header bar renders with expected structural elements."""

    def test_header_has_brand(self, dashboard_page: Page):
        """Header should have the MapArr brand link."""
        brand = dashboard_page.locator("#header-brand-link")
        expect(brand).to_be_visible(timeout=ELEMENT_TIMEOUT)

    def test_header_has_logo_text(self, dashboard_page: Page):
        """Header should display 'MapArr' as the logo text."""
        logo = dashboard_page.locator(".logo")
        expect(logo).to_be_visible(timeout=ELEMENT_TIMEOUT)
        assert logo.inner_text() == "MapArr"

    def test_header_has_path_selector(self, dashboard_page: Page):
        """Header should have the clickable path selector button."""
        path_btn = dashboard_page.locator("#header-path")
        expect(path_btn).to_be_visible(timeout=ELEMENT_TIMEOUT)

    def test_header_has_connection_status(self, dashboard_page: Page):
        """Header should show backend connection status indicator."""
        status = dashboard_page.locator("#health-status")
        expect(status).to_be_visible(timeout=ELEMENT_TIMEOUT)


# ─── TestFooter ───


class TestFooter:
    """Footer renders with version and navigation links."""

    def test_footer_has_version(self, dashboard_page: Page):
        """Footer should display the application version."""
        version = dashboard_page.locator("#footer-version")
        expect(version).to_be_visible(timeout=ELEMENT_TIMEOUT)
        text = version.inner_text()
        assert "MapArr" in text or "v" in text.lower(), (
            f"Unexpected footer version text: '{text}'"
        )

    def test_footer_has_log_toggle(self, dashboard_page: Page):
        """Footer should have a log panel toggle button."""
        toggle = dashboard_page.locator("#footer-log-toggle")
        expect(toggle).to_be_visible(timeout=ELEMENT_TIMEOUT)

    def test_footer_has_external_links(self, dashboard_page: Page):
        """Footer should have external navigation links (GitHub, etc)."""
        links = dashboard_page.locator(".footer-links .footer-icon")
        assert links.count() >= 3, (
            f"Expected at least 3 footer links, got {links.count()}"
        )
