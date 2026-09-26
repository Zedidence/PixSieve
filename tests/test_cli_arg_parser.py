"""
Unit tests for pixsieve/cli/arg_parser.py.
"""

from pathlib import Path

import pytest

from pixsieve.cli.arg_parser import create_parser


class TestBoundedIntArgs:
    """Numeric CLI args must reject out-of-range values with a clean usage
    error instead of silently accepting anything, matching the same bounds
    the web API's pydantic schemas already enforce."""

    def test_duplicates_workers_out_of_range_rejected(self):
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['duplicates', '/x', '--workers', '9999'])

    def test_duplicates_workers_in_range_accepted(self):
        parser = create_parser()
        args = parser.parse_args(['duplicates', '/x', '--workers', '8'])
        assert args.workers == 8

    def test_duplicates_threshold_out_of_range_rejected(self):
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['duplicates', '/x', '--threshold', '100'])

    def test_rename_random_length_out_of_range_rejected(self):
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['rename', '/x', '--mode', 'random', '--length', '2'])

    def test_convert_quality_out_of_range_rejected(self):
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['convert', '/x', '--quality', '0'])

    def test_convert_quality_in_range_accepted(self):
        parser = create_parser()
        args = parser.parse_args(['convert', '/x', '--quality', '80'])
        assert args.quality == 80


class TestDuplicatesRecursiveFlag:
    """The duplicates subcommand's --no-recursive now matches every other
    subcommand's flag (via the shared _add_recursive_arg helper) instead of
    hand-rolling its own confusing '-r means off' short flag."""

    def test_no_recursive_long_flag_works(self):
        parser = create_parser()
        args = parser.parse_args(['duplicates', '/x', '--no-recursive'])
        assert args.no_recursive is True

    def test_short_r_flag_no_longer_recognized(self):
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(['duplicates', '/x', '-r'])


class TestWorkerArgs:
    """-w/--workers defaults to None (auto, drive-aware) on every command
    that runs a thread pool; --storage-profile corrects a misdetected drive."""

    @pytest.mark.parametrize('argv', [
        ['duplicates', '/x'],
        ['move-to-parent', '/x'],
        ['move', '/x', '/y'],
        ['rename', 'random', '/x'],
        ['metadata', 'randomize-dates', '/x', '--start', '2020-01-01', '--end', '2021-01-01'],
        ['pipeline', '/x', '--steps', 'cleanup_empty'],
    ])
    def test_default_is_auto(self, argv):
        args = create_parser().parse_args(argv)
        assert args.workers is None
        assert args.storage_profile is None
        assert args.no_io_probe is False

    def test_explicit_auto(self):
        args = create_parser().parse_args(['duplicates', '/x', '-w', 'auto'])
        assert args.workers is None

    def test_explicit_count(self):
        args = create_parser().parse_args(['move', '/x', '/y', '-w', '3'])
        assert args.workers == 3

    @pytest.mark.parametrize('argv', [
        ['duplicates', '/x', '-w', '0'],
        ['duplicates', '/x', '-w', '33'],
        ['rename', 'random', '/x', '-w', '17'],
        ['move', '/x', '/y', '-w', 'many'],
    ])
    def test_out_of_range_rejected(self, argv):
        with pytest.raises(SystemExit):
            create_parser().parse_args(argv)

    def test_storage_profile_repeatable(self):
        args = create_parser().parse_args([
            'move', '/x', '/y', '--storage-profile', 'E:=usb-hdd', '--storage-profile', 'nvme',
        ])
        assert args.storage_profile == ['E:=usb-hdd', 'nvme']

    def test_storage_profile_unknown_type_rejected(self):
        with pytest.raises(SystemExit):
            create_parser().parse_args(['duplicates', '/x', '--storage-profile', 'floppy'])

    def test_storage_options(self):
        from pixsieve.cli.arg_parser import storage_options
        args = create_parser().parse_args(
            ['duplicates', '/x', '--storage-profile', '/mnt=network', '--no-io-probe'])
        assert storage_options(args) == {'overrides': {'/mnt': 'network'}, 'allow_probe': False}

    def test_interactive_defaults_are_auto(self):
        from pixsieve.cli.arg_parser import parse_arguments
        args = parse_arguments([])
        assert args.workers is None
        assert args.storage_profile is None


class TestStorageCommand:
    def test_parses(self):
        args = create_parser().parse_args(['storage', 'C:/', '/mnt/x', '--probe', '--json'])
        assert args.command == 'storage'
        assert args.paths == [Path('C:/'), Path('/mnt/x')]
        assert args.probe and args.json

    def test_is_a_valid_command(self):
        from pixsieve.cli.arg_parser import parse_arguments
        assert parse_arguments(['storage', '/x']).command == 'storage'

    def test_reports_drive_and_worker_counts(self, tmp_path, monkeypatch, capsys):
        import json
        import logging
        from pixsieve import config
        from pixsieve.cli.operations_orchestrator import OperationsOrchestrator
        from pixsieve.utils import disk_type
        from pixsieve.utils.disk_type import Bus, DriveProfile, Media

        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        monkeypatch.setattr(disk_type, 'detect_drive',
                            lambda p: DriveProfile('k', Media.HDD, Bus.SATA, detail='HDD|SATA|7200'))
        args = create_parser().parse_args(['storage', str(tmp_path), '--json'])
        assert OperationsOrchestrator(args, logging.getLogger('t')).run() == 0

        report = json.loads(capsys.readouterr().out)
        assert report[0]['drive']['label'] == 'internal HDD'
        assert report[0]['workers']['scan'] == 4
        assert report[0]['workers']['copy'] == 1
        assert report[0]['workers']['metadata'] == 2

    def test_text_output_with_override(self, tmp_path, monkeypatch, capsys):
        import logging
        from pixsieve import config
        from pixsieve.cli.operations_orchestrator import OperationsOrchestrator

        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        args = create_parser().parse_args(['storage', str(tmp_path), '--storage-profile', 'usb-ssd'])
        assert OperationsOrchestrator(args, logging.getLogger('t')).run() == 0
        out = capsys.readouterr().out
        assert 'USB SSD' in out
        assert 'override' in out
        assert 'copy 2' in out

    def test_missing_path(self, tmp_path):
        import logging
        from pixsieve.cli.operations_orchestrator import OperationsOrchestrator
        args = create_parser().parse_args(['storage', str(tmp_path / 'nope')])
        assert OperationsOrchestrator(args, logging.getLogger('t')).run() == 1
