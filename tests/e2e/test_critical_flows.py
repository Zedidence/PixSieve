"""
E2E tests for critical PixSieve user flows using Playwright.

Requires a running server: ``python -m pixsieve.app --no-browser``
Run with: ``pytest tests/e2e/ --base-url http://localhost:5000``
"""

import os
import re
import tempfile

import pytest

# Skip the whole module gracefully if playwright is not installed
pytest.importorskip('playwright')

from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _server_url(base_url: str, path: str) -> str:
    return base_url.rstrip('/') + path


# ---------------------------------------------------------------------------
# App shell loads
# ---------------------------------------------------------------------------

class TestAppShell:
    def test_page_title(self, page: Page, base_url: str):
        """Home page loads and shows the PixSieve heading."""
        page.goto(base_url)
        expect(page).to_have_title(re.compile(r'PixSieve', re.IGNORECASE))

    def test_header_visible(self, page: Page, base_url: str):
        page.goto(base_url)
        heading = page.locator('header h1')
        expect(heading).to_be_visible()
        expect(heading).to_contain_text('PixSieve')

    def test_tab_navigation(self, page: Page, base_url: str):
        """Clicking the Operations tab switches the visible panel."""
        page.goto(base_url)
        ops_tab = page.locator('[data-tab="operations"]')
        expect(ops_tab).to_be_visible()
        ops_tab.click()
        ops_panel = page.locator('#tab-operations')
        expect(ops_panel).to_have_class(re.compile(r'active'))

    def test_theme_toggle(self, page: Page, base_url: str):
        """Theme toggle button switches between dark and light modes."""
        page.goto(base_url)
        toggle = page.locator('.theme-toggle')
        # Read initial theme
        initial = page.locator('html').get_attribute('data-theme')
        toggle.click()
        after = page.locator('html').get_attribute('data-theme')
        assert initial != after, 'Theme should change after clicking toggle'

    def test_swagger_docs(self, page: Page, base_url: str):
        """Swagger UI is served at /docs/."""
        page.goto(_server_url(base_url, '/docs/'))
        expect(page).to_have_url(re.compile(r'/docs/'))
        expect(page.locator('.swagger-ui')).to_be_visible(timeout=10_000)


# ---------------------------------------------------------------------------
# Scan form
# ---------------------------------------------------------------------------

class TestScanForm:
    def test_directory_input_present(self, page: Page, base_url: str):
        page.goto(base_url)
        inp = page.locator('.scan-folder-row .pf-path').first
        expect(inp).to_be_visible()

    def test_start_scan_empty_directory(self, page: Page, base_url: str):
        """Starting a scan with no directory shows an error toast."""
        page.goto(base_url)
        page.locator('[data-action="start-scan"]').click()
        # Toast or inline error should appear
        error = page.locator('.toast, .error-toast, [class*="error"]').first
        expect(error).to_be_visible(timeout=3_000)

    def test_start_scan_invalid_directory(self, page: Page, base_url: str):
        """A non-existent path triggers a validation error from the API."""
        page.goto(base_url)
        page.fill('.scan-folder-row .pf-path', '/this/path/does/not/exist/at/all')
        page.locator('[data-action="start-scan"]').click()
        error = page.locator('.toast, .error-toast, [class*="error"]').first
        expect(error).to_be_visible(timeout=5_000)

    def test_scan_with_real_directory(self, page: Page, base_url: str):
        """A scan of a real (temp) directory runs and reaches complete state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two identical small image files so there's at least one duplicate
            try:
                from PIL import Image
                img = Image.new('RGB', (10, 10), color=(128, 64, 32))
                img.save(os.path.join(tmpdir, 'a.jpg'))
                img.save(os.path.join(tmpdir, 'b.jpg'))
            except ImportError:
                pytest.skip('Pillow not available')

            page.goto(base_url)
            page.fill('.scan-folder-row .pf-path', tmpdir)
            page.locator('[data-action="start-scan"]').click()

            # Wait for scan to complete (up to 30 s)
            stats_bar = page.locator('#statsBar')
            expect(stats_bar).to_be_visible(timeout=30_000)


# ---------------------------------------------------------------------------
# Results view
# ---------------------------------------------------------------------------

class TestResultsView:
    @pytest.fixture(autouse=True)
    def _with_results(self, page: Page, base_url: str):
        """Seed results by running a scan against a temp directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                from PIL import Image
                img = Image.new('RGB', (10, 10), color=(0, 0, 0))
                img.save(os.path.join(tmpdir, 'dup1.jpg'))
                img.save(os.path.join(tmpdir, 'dup2.jpg'))
            except ImportError:
                pytest.skip('Pillow not available')

            page.goto(base_url)
            page.fill('.scan-folder-row .pf-path', tmpdir)
            page.locator('[data-action="start-scan"]').click()
            # Wait for stats bar (scan complete)
            expect(page.locator('#statsBar')).to_be_visible(timeout=30_000)
            yield

    def test_stats_bar_shows_groups(self, page: Page, base_url: str):
        groups_val = page.locator('#statGroups').text_content()
        assert groups_val is not None and int(groups_val) >= 1

    def test_filter_bar_visible(self, page: Page, base_url: str):
        expect(page.locator('#filterBar')).to_be_visible()

    def test_view_toggle_switches_to_list(self, page: Page, base_url: str):
        page.locator('[data-view="list"]').click()
        # Group container should now contain list-view elements
        expect(page.locator('.group-images-list').first).to_be_visible()

    def test_view_toggle_switches_to_compare(self, page: Page, base_url: str):
        page.locator('[data-view="compare"]').click()
        expect(page.locator('.group-images-compare').first).to_be_visible()

    def test_compare_slider_opens_for_pair(self, page: Page, base_url: str):
        """Slider button appears for groups with exactly 2 images and opens overlay."""
        slider_btn = page.locator('.compare-slider-btn').first
        if slider_btn.count() == 0:
            pytest.skip('No 2-image groups in test data')
        slider_btn.click()
        expect(page.locator('#compareSliderOverlay')).to_have_class(re.compile(r'active'))

    def test_export_dropdown_opens(self, page: Page, base_url: str):
        page.locator('[data-action="toggle-export-menu"]').click()
        expect(page.locator('#exportMenu')).to_have_class(re.compile(r'open'))

    def test_action_bar_visible(self, page: Page, base_url: str):
        expect(page.locator('#actionBar')).to_be_visible()


# ---------------------------------------------------------------------------
# API health checks
# ---------------------------------------------------------------------------

class TestAPIHealth:
    def test_ping(self, page: Page, base_url: str):
        resp = page.request.get(_server_url(base_url, '/api/ping'))
        assert resp.ok
        assert resp.json()['status'] == 'ok'

    def test_status_endpoint(self, page: Page, base_url: str):
        resp = page.request.get(_server_url(base_url, '/api/status'))
        assert resp.ok
        data = resp.json()
        assert 'status' in data

    def test_cache_stats(self, page: Page, base_url: str):
        resp = page.request.get(_server_url(base_url, '/api/cache/stats'))
        assert resp.ok
