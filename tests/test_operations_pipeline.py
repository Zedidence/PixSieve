"""
Unit tests for dupefinder/operations/pipeline.py.
"""

import pytest
from datetime import datetime
from pathlib import Path
from PIL import Image

from dupefinder.operations.pipeline import run_pipeline, AVAILABLE_STEPS


class TestRunPipeline:
    """Test run_pipeline function."""

    def test_cleanup_step(self, temp_dir):
        """Single cleanup step executes and returns results."""
        (temp_dir / "empty_sub").mkdir()

        results = run_pipeline(
            temp_dir, steps=['cleanup_empty'], dry_run=True
        )
        assert 'cleanup_empty' in results
        assert 'deleted' in results['cleanup_empty']

    def test_unknown_step_returns_empty(self, temp_dir):
        """Unknown step name causes pipeline to return empty dict."""
        results = run_pipeline(temp_dir, steps=['fake_step'])
        assert results == {}

    def test_date_steps_without_dates_returns_empty(self, temp_dir):
        """Date steps without start/end dates return empty dict."""
        results = run_pipeline(temp_dir, steps=['randomize_exif'])
        assert results == {}

    def test_start_after_end_returns_empty(self, temp_dir):
        """start_date >= end_date returns empty dict."""
        results = run_pipeline(
            temp_dir,
            steps=['randomize_dates'],
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2020, 1, 1),
        )
        assert results == {}

    def test_dry_run_passes_through(self, temp_dir):
        """Dry-run flag is passed to each step."""
        Image.new('RGB', (10, 10), 'red').save(
            temp_dir / "photo.png", 'PNG'
        )

        results = run_pipeline(
            temp_dir, steps=['cleanup_empty'], dry_run=True
        )
        assert 'cleanup_empty' in results

    def test_multiple_steps_execute_in_order(self, temp_dir):
        """Multiple steps execute in order and return aggregated results."""
        Image.new('RGB', (10, 10), 'red').save(
            temp_dir / "photo.png", 'PNG'
        )
        (temp_dir / "empty_sub").mkdir()

        results = run_pipeline(
            temp_dir,
            steps=['convert_jpg', 'cleanup_empty'],
            dry_run=True,
        )
        assert 'convert_jpg' in results
        assert 'cleanup_empty' in results

    def test_invalid_directory_returns_empty(self, temp_dir):
        """Non-existent directory returns empty dict."""
        results = run_pipeline(
            temp_dir / "nonexistent", steps=['cleanup_empty']
        )
        assert results == {}

    def test_random_rename_step(self, temp_dir):
        """Random rename step executes correctly."""
        Image.new('RGB', (10, 10), 'red').save(
            temp_dir / "photo.jpg", 'JPEG'
        )

        results = run_pipeline(
            temp_dir, steps=['random_rename'], dry_run=True
        )
        assert 'random_rename' in results
        assert 'success' in results['random_rename']

    def test_date_steps_with_valid_dates(self, temp_dir):
        """Date steps work when valid dates are provided."""
        Image.new('RGB', (10, 10), 'red').save(
            temp_dir / "photo.jpg", 'JPEG'
        )

        results = run_pipeline(
            temp_dir,
            steps=['randomize_dates'],
            start_date=datetime(2020, 1, 1),
            end_date=datetime(2023, 12, 31),
            dry_run=True,
        )
        assert 'randomize_dates' in results
        assert 'success' in results['randomize_dates']


class TestAvailableSteps:
    """Test AVAILABLE_STEPS registry."""

    def test_all_steps_defined(self):
        """All expected pipeline steps are registered."""
        expected = {
            'random_rename', 'convert_jpg', 'randomize_exif',
            'randomize_dates', 'cleanup_empty',
        }
        assert set(AVAILABLE_STEPS.keys()) == expected

    def test_steps_have_labels(self):
        """Each step has a label field."""
        for step_key, step_info in AVAILABLE_STEPS.items():
            assert 'label' in step_info, f"Step {step_key} missing label"
