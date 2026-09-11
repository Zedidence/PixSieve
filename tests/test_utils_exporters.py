"""
Unit tests for pixsieve/utils/exporters.py.
"""

import csv

from pixsieve.models import DuplicateGroup, ImageInfo
from pixsieve.utils.exporters import export_results


def _make_group(group_id, path, **kwargs):
    img = ImageInfo(
        path=path,
        file_size=kwargs.get('file_size', 1000),
        width=kwargs.get('width', 100),
        height=kwargs.get('height', 100),
        quality_score=kwargs.get('quality_score', 5.0),
    )
    return DuplicateGroup(id=group_id, images=[img], match_type=kwargs.get('match_type', 'exact'))


class TestExportCsv:
    def test_round_trips_path_with_comma_and_quote(self, temp_dir):
        """A path containing a literal comma and quote must not corrupt the CSV row."""
        tricky_path = str(temp_dir / 'My "Vacation", 2024.jpg')
        group = _make_group(1, tricky_path)

        out_file = temp_dir / 'results.csv'
        export_results([group], [], out_file, export_format='csv', selections={tricky_path: 'keep'})

        with open(out_file, encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 1
        assert rows[0]['path'] == tricky_path
        assert rows[0]['status'] == 'keep'
        assert rows[0]['group_id'] == '1'
        assert rows[0]['match_type'] == 'exact'

    def test_normal_paths_export_cleanly(self, temp_dir):
        """Plain paths (no special characters) still round-trip correctly."""
        path1 = str(temp_dir / 'a.jpg')
        path2 = str(temp_dir / 'b.jpg')
        exact_group = _make_group(1, path1)
        perceptual_group = _make_group(2, path2, match_type='perceptual')

        out_file = temp_dir / 'results.csv'
        export_results(
            [exact_group], [perceptual_group], out_file,
            export_format='csv', selections={path1: 'keep', path2: 'delete'},
        )

        with open(out_file, encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 2
        assert rows[0]['path'] == path1
        assert rows[0]['status'] == 'keep'
        assert rows[0]['match_type'] == 'exact'
        assert rows[1]['path'] == path2
        assert rows[1]['status'] == 'duplicate'
        assert rows[1]['match_type'] == 'perceptual'
