# PixSieve Test Suite

This directory contains the test suite for the PixSieve application.

## Running Tests

### Install Test Dependencies

```bash
# Install package with dev dependencies
pip install -e ".[dev]"

# Or install pytest manually
pip install pytest pytest-cov
```

### Run All Tests

```bash
# From project root
pytest

# Or with coverage
pytest --cov=pixsieve --cov-report=html
```

### Run Specific Test Files

```bash
# Run only model tests
pytest tests/test_models.py

# Run only scanner tests
pytest tests/test_scanner.py

# Run only LSH tests
pytest tests/test_lsh.py

# Run only database tests
pytest tests/test_database.py
```

### Run Specific Test Classes or Functions

```bash
# Run a specific test class
pytest tests/test_models.py::TestImageInfo

# Run a specific test function
pytest tests/test_scanner.py::TestFindImageFiles::test_find_png_files
```

### Additional Options

```bash
# Verbose output
pytest -v

# Stop on first failure
pytest -x

# Show local variables on failure
pytest -l

# Run tests in parallel (requires pytest-xdist)
pytest -n auto
```

## Test Structure

### Unit / Integration Tests (`tests/`)

- `conftest.py` — Shared fixtures and test configuration
- `test_models.py` — Tests for ImageInfo and DuplicateGroup classes
- `test_scanner.py` — Tests for image scanning and analysis functions
- `test_scan_orchestrator.py` — Tests for the API scan orchestrator (multi-directory scans, reference folders, video inclusion)
- `test_video.py` — Tests for video analysis and video duplicate detection (`pixsieve.scanner.video_analysis` / `video_deduplication`)
- `test_lsh.py` — Tests for LSH (Locality-Sensitive Hashing) implementation
- `test_database.py` — Tests for SQLite caching functionality
- `test_api_operations.py` — Tests for file-operation API endpoints
- `test_cli_operations.py` — Tests for file-operation CLI subcommands
- `test_operations_*.py` — Per-module operation tests: cleanup, convert, metadata, move, pipeline, ratings, rename, sort
- `fixtures/` — Test data and sample images (auto-generated)

Current count: **307 tests** total (288 in `tests/`, 19 in `tests/e2e/`) — run `pytest tests/ --collect-only -q` to reconfirm after adding tests.

### End-to-End Tests (`tests/e2e/`)

Browser-level tests using **Playwright**. Require a running PixSieve server.

**Setup:**
```bash
pip install playwright pytest-playwright
# or via extras: pip install -e ".[e2e]"
playwright install chromium
```

**Run:**
```bash
# Start the server first
python -m pixsieve.app --no-browser &

# Run E2E suite
pytest tests/e2e/ --base-url http://localhost:5000
```

**Coverage:** app shell, Swagger UI, scan form validation, results view (stats, filters, view toggles, compare slider, export dropdown), API health checks.

## Test Coverage

To generate a coverage report:

```bash
pytest --cov=pixsieve --cov-report=html
```

Then open `htmlcov/index.html` in your browser to view the detailed coverage report.

## Writing New Tests

When adding new functionality, please add corresponding tests:

1. Create test functions with descriptive names starting with `test_`
2. Use appropriate fixtures from `conftest.py`
3. Follow the Arrange-Act-Assert pattern
4. Add docstrings explaining what the test validates

Example:

```python
def test_my_new_feature(sample_images):
    """Test that my new feature works correctly."""
    # Arrange
    input_data = sample_images['unique']

    # Act
    result = my_new_function(input_data)

    # Assert
    assert result is not None
    assert result.some_property == expected_value
```

## Continuous Integration

Tests are automatically run on:
- Every push to main branch
- Every pull request
- Nightly builds (if configured)

Make sure all tests pass before submitting a pull request.
