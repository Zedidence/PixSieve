"""
Playwright E2E test configuration.

Usage:
    pip install playwright pytest-playwright
    playwright install chromium
    pytest tests/e2e/ --base-url http://localhost:5000

The tests require a running PixSieve server. Start it first with:
    python -m pixsieve.app --no-browser
"""

import pytest


@pytest.fixture(scope='session')
def base_url():
    """Override with --base-url CLI option or default to localhost:5000."""
    return 'http://localhost:5000'
