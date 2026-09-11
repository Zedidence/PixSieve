"""
Unit tests for pixsieve/cli/arg_parser.py.
"""

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
