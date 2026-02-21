"""
Unit tests for dupefinder/cli/operations_orchestrator.py.
"""

import pytest
import logging
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from dupefinder.cli.operations_orchestrator import OperationsOrchestrator


@pytest.fixture
def logger():
    """Create a test logger."""
    return logging.getLogger("test_cli_ops")


def make_args(command, directory, **kwargs):
    """Helper to create a Namespace object simulating parsed CLI args."""
    defaults = {
        'command': command,
        'directory': Path(directory),
        'no_dry_run': False,
        'verbose': False,
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


class TestOperationsOrchestratorDispatch:
    """Test command dispatching."""

    def test_unknown_command_returns_1(self, temp_dir, logger):
        """Unknown command returns exit code 1."""
        args = make_args('unknown-command', temp_dir)
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 1

    def test_dry_run_defaults_to_true(self, temp_dir, logger):
        """Dry-run is True when no_dry_run is not set."""
        args = make_args('cleanup', temp_dir)
        orch = OperationsOrchestrator(args, logger)
        assert orch.dry_run is True

    def test_dry_run_false_when_no_dry_run_set(self, temp_dir, logger):
        """Dry-run is False when no_dry_run is True."""
        args = make_args('cleanup', temp_dir, no_dry_run=True)
        orch = OperationsOrchestrator(args, logger)
        assert orch.dry_run is False


class TestHandleCleanup:
    """Test cleanup command handler."""

    def test_cleanup_success(self, temp_dir, logger):
        """Cleanup command returns 0 on success."""
        (temp_dir / "empty_sub").mkdir()
        args = make_args('cleanup', temp_dir)
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0

    def test_cleanup_missing_directory(self, temp_dir, logger):
        """Cleanup with non-existent directory returns 1."""
        args = make_args('cleanup', temp_dir / 'nonexistent')
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 1


class TestHandleMoveToParent:
    """Test move-to-parent command handler."""

    def test_move_to_parent_success(self, ops_temp_dir, logger):
        """Move-to-parent returns 0 on success."""
        args = make_args('move-to-parent', ops_temp_dir)
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0

    def test_with_extensions_filter(self, ops_temp_dir, logger):
        """Extensions filter is passed correctly."""
        args = make_args(
            'move-to-parent', ops_temp_dir, extensions=['.jpg']
        )
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0


class TestHandleMove:
    """Test move command handler."""

    def test_move_success(self, ops_temp_dir, temp_dir, logger):
        """Move command returns 0 on success."""
        dest = temp_dir / "dest"
        args = make_args('move', ops_temp_dir, destination=dest)
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0


class TestHandleRename:
    """Test rename command handler."""

    def test_rename_random(self, ops_temp_dir, logger):
        """Rename random subcommand returns 0."""
        args = make_args(
            'rename', ops_temp_dir, rename_mode='random',
            length=12, workers=1, no_recursive=False,
        )
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0

    def test_rename_parent(self, ops_temp_dir, logger):
        """Rename parent subcommand returns 0."""
        args = make_args('rename', ops_temp_dir, rename_mode='parent')
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0

    def test_unknown_rename_mode(self, ops_temp_dir, logger):
        """Unknown rename mode returns 1."""
        args = make_args('rename', ops_temp_dir, rename_mode='unknown')
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 1


class TestHandleSort:
    """Test sort command handler."""

    def test_sort_alpha(self, ops_temp_dir, logger):
        """Sort alpha subcommand returns 0."""
        args = make_args('sort', ops_temp_dir, sort_mode='alpha')
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0

    def test_unknown_sort_mode(self, ops_temp_dir, logger):
        """Unknown sort mode returns 1."""
        args = make_args('sort', ops_temp_dir, sort_mode='unknown')
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 1


class TestHandleConvert:
    """Test convert command handler."""

    def test_convert_success(self, ops_temp_dir, logger):
        """Convert command returns 0."""
        args = make_args(
            'convert', ops_temp_dir,
            quality=95, delete_originals=False, no_recursive=False,
        )
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0


class TestHandleFixExtensions:
    """Test fix-extensions command handler."""

    def test_fix_extensions_success(self, ops_temp_dir, logger):
        """Fix-extensions command returns 0."""
        args = make_args('fix-extensions', ops_temp_dir, no_recursive=False)
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0


class TestHandleMetadata:
    """Test metadata command handler."""

    def test_randomize_dates_success(self, ops_temp_dir, logger):
        """Metadata randomize-dates returns 0 with valid dates."""
        args = make_args(
            'metadata', ops_temp_dir,
            metadata_mode='randomize-dates',
            start='2020-01-01', end='2023-12-31',
            no_recursive=False,
        )
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0

    def test_invalid_start_date(self, ops_temp_dir, logger):
        """Invalid start date raises ValueError (parse_date doesn't return None)."""
        args = make_args(
            'metadata', ops_temp_dir,
            metadata_mode='randomize-dates',
            start='bad-date', end='2023-12-31',
        )
        orch = OperationsOrchestrator(args, logger)
        with pytest.raises(ValueError):
            orch.run()

    def test_unknown_metadata_mode(self, ops_temp_dir, logger):
        """Unknown metadata mode returns 1."""
        args = make_args(
            'metadata', ops_temp_dir,
            metadata_mode='unknown',
            start='2020-01-01', end='2023-12-31',
        )
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 1


class TestHandlePipeline:
    """Test pipeline command handler."""

    def test_pipeline_cleanup(self, ops_temp_dir, logger):
        """Pipeline with cleanup step returns 0."""
        args = make_args(
            'pipeline', ops_temp_dir,
            steps='cleanup_empty',
        )
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 0

    def test_pipeline_date_steps_missing_dates(self, ops_temp_dir, logger):
        """Pipeline with date steps and no dates returns 1."""
        args = make_args(
            'pipeline', ops_temp_dir,
            steps='randomize_dates',
            start=None, end=None,
        )
        orch = OperationsOrchestrator(args, logger)
        assert orch.run() == 1
