"""
Every file operation may only touch image files (and video files when asked
to). Documents, archives, sidecars and anything else living in a photo
folder must be left exactly where and as they are.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from pixsieve import config
from pixsieve.config import IMAGE_EXTENSIONS, is_media_file, media_only, resolve_extensions
from pixsieve.operations import (
    move_to_parent, move_with_structure, randomize_dates, rename_by_parent, rename_random,
    sort_alphabetical,
)

OTHER_FILES = ['notes.txt', 'report.pdf', 'backup.zip', 'photo.jpg.xmp', 'desktop.ini', 'README']


def _image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), 'red').save(path)
    return path


def _others(folder: Path) -> dict[Path, bytes]:
    """Create non-media files; returns {path: contents} to verify later."""
    folder.mkdir(parents=True, exist_ok=True)
    created = {}
    for name in OTHER_FILES:
        p = folder / name
        p.write_bytes(f'keep {name}'.encode())
        created[p] = p.read_bytes()
    return created


def _assert_untouched(files: dict[Path, bytes]):
    for path, contents in files.items():
        assert path.exists(), f"{path.name} was moved or renamed"
        assert path.read_bytes() == contents, f"{path.name} was modified"


class TestExtensionHelpers:
    def test_media_only_drops_everything_else(self):
        assert media_only({'.JPG', 'png', '.txt', '.pdf', '.mp4', ''}) == {'.jpg', '.png', '.mp4'}
        assert media_only(None) == set()

    def test_is_media_file(self):
        assert is_media_file('/a/B.JPEG')
        assert is_media_file('clip.MOV')
        assert is_media_file('raw.sr2')
        assert not is_media_file('notes.txt')
        assert not is_media_file('photo.jpg.xmp')
        assert not is_media_file('README')

    def test_resolve_extensions_never_returns_non_media(self):
        assert resolve_extensions(IMAGE_EXTENSIONS, extra={'.txt', '.docx'}) == set()
        assert resolve_extensions(IMAGE_EXTENSIONS, extra={'.txt', '.jpg'}) == {'.jpg'}
        assert '.mp4' in resolve_extensions(IMAGE_EXTENSIONS, include_videos=True)
        assert '.mp4' not in resolve_extensions(IMAGE_EXTENSIONS)

    def test_rating_extensions_are_media(self):
        assert config.RATING_EXTENSIONS <= config.MEDIA_EXTENSIONS
        assert config.CONVERTIBLE_TO_JPG <= config.MEDIA_EXTENSIONS
        assert config.EXIF_EXTENSIONS <= config.MEDIA_EXTENSIONS


class TestOperations:
    def test_rename_random(self, tmp_path):
        img = _image(tmp_path / 'a.jpg')
        others = _others(tmp_path)
        rename_random(tmp_path, dry_run=False)
        assert not img.exists()
        _assert_untouched(others)

    def test_rename_random_ignores_non_media_extension_list(self, tmp_path):
        others = _others(tmp_path)
        stats = rename_random(tmp_path, extensions={'.txt', '.pdf', '.zip'}, dry_run=False)
        assert stats.get('renamed', 0) == 0
        _assert_untouched(others)

    def test_rename_by_parent(self, tmp_path):
        album = tmp_path / 'Artist' / 'Album'
        img = _image(album / 'photo.jpg')
        others = _others(album)
        rename_by_parent(tmp_path, dry_run=False)
        assert not img.exists()
        assert (album / 'Artist_Album_1.jpg').exists()
        _assert_untouched(others)

    def test_rename_by_parent_videos_opt_in(self, tmp_path):
        album = tmp_path / 'Artist' / 'Album'
        clip = album / 'clip.mp4'
        album.mkdir(parents=True)
        clip.write_bytes(b'video')
        rename_by_parent(tmp_path, dry_run=False)
        assert clip.exists()
        rename_by_parent(tmp_path, dry_run=False,
                         extensions=resolve_extensions(IMAGE_EXTENSIONS, include_videos=True))
        assert not clip.exists()

    def test_move_to_parent(self, tmp_path):
        sub = tmp_path / 'sub'
        img = _image(sub / 'a.jpg')
        others = _others(sub)
        move_to_parent(tmp_path, dry_run=False)
        assert not img.exists() and (tmp_path / 'a.jpg').exists()
        _assert_untouched(others)

    def test_move_with_structure(self, tmp_path):
        src, dst = tmp_path / 'src', tmp_path / 'dst'
        img = _image(src / 'nested' / 'a.jpg')
        others = _others(src / 'nested')
        docs_only = _others(src / 'docs')
        move_with_structure(src, dst, dry_run=False)
        assert not img.exists() and (dst / 'nested' / 'a.jpg').exists()
        _assert_untouched(others)
        _assert_untouched(docs_only)
        # Folders without media aren't mirrored at the destination
        assert not (dst / 'docs').exists()

    def test_move_with_structure_dry_run_creates_nothing(self, tmp_path):
        src, dst = tmp_path / 'src', tmp_path / 'dst'
        _image(src / 'a.jpg')
        move_with_structure(src, dst, dry_run=True)
        assert not dst.exists()

    def test_move_with_structure_videos_opt_in(self, tmp_path):
        src, dst = tmp_path / 'src', tmp_path / 'dst'
        src.mkdir()
        (src / 'clip.mov').write_bytes(b'video')
        move_with_structure(src, dst, dry_run=False)
        assert (src / 'clip.mov').exists()
        move_with_structure(src, dst, dry_run=False,
                            extensions=resolve_extensions(IMAGE_EXTENSIONS, include_videos=True))
        assert (dst / 'clip.mov').exists()

    def test_sort_alphabetical(self, tmp_path):
        img = _image(tmp_path / 'apple.jpg')
        others = _others(tmp_path)
        sort_alphabetical(tmp_path, dry_run=False)
        assert not img.exists() and (tmp_path / 'A-G' / 'apple.jpg').exists()
        _assert_untouched(others)

    def test_randomize_dates_leaves_other_files_alone(self, tmp_path):
        _image(tmp_path / 'a.jpg')
        others = _others(tmp_path)
        mtimes = {p: p.stat().st_mtime for p in others}
        randomize_dates(tmp_path, datetime(2001, 1, 1), datetime(2002, 1, 1), dry_run=False,
                        extensions={'.txt', '.pdf', '.jpg'})
        _assert_untouched(others)
        assert {p: p.stat().st_mtime for p in others} == mtimes


class TestCli:
    @pytest.mark.parametrize('argv', [
        ['move', '.', '/dst', '--include-videos'],
        ['rename', 'parent', '.', '--include-videos'],
        ['sort', 'alpha', '.', '--include-videos'],
    ])
    def test_include_videos_flag(self, argv):
        from pixsieve.cli.arg_parser import create_parser
        assert create_parser().parse_args(argv).include_videos is True


class TestApi:
    @pytest.fixture
    def scanned(self, tmp_path, monkeypatch):
        from pixsieve.api import routes
        monkeypatch.setattr(routes.scan_state, 'directories',
                            [{'path': str(tmp_path), 'is_reference': False}])
        return tmp_path

    def test_rename_image_rejects_non_media(self, flask_client, scanned):
        doc = scanned / 'notes.txt'
        doc.write_text('keep')
        resp = flask_client.post('/api/image/rename',
                                 json={'path': str(doc), 'newName': 'renamed.jpg'})
        assert resp.status_code == 400
        assert doc.exists()

    def test_rename_image_rejects_non_media_new_name(self, flask_client, scanned):
        img = _image(scanned / 'a.jpg')
        resp = flask_client.post('/api/image/rename', json={'path': str(img), 'newName': 'a.txt'})
        assert resp.status_code == 400
        assert img.exists()

    def test_rename_image_allows_media(self, flask_client, scanned):
        img = _image(scanned / 'a.jpg')
        resp = flask_client.post('/api/image/rename', json={'path': str(img), 'newName': 'b.png'})
        assert resp.status_code == 200
        assert (scanned / 'b.png').exists()

    def test_delete_rejects_non_media(self, flask_client, scanned):
        doc = scanned / 'notes.txt'
        doc.write_text('keep')
        img = _image(scanned / 'a.jpg')
        resp = flask_client.post('/api/delete', json={
            'files': [str(img), str(doc)], 'trashDir': str(scanned / 'trash'),
        })
        assert resp.status_code == 400
        assert str(doc) in resp.get_json()['invalid_paths']
        assert doc.exists() and img.exists()

    def test_batch_operation_rejects_non_media(self, flask_client, scanned):
        doc = scanned / 'notes.txt'
        doc.write_text('keep')
        resp = flask_client.post('/api/batch-operation', json={
            'operation': 'move', 'files': [str(doc)], 'destination': str(scanned / 'out'),
        })
        assert resp.status_code == 400
        assert doc.exists()

    @pytest.mark.parametrize('endpoint', [
        '/api/operations/rename/parent', '/api/operations/sort/alpha',
    ])
    def test_ops_accept_include_videos(self, flask_client, tmp_path, endpoint):
        import pixsieve.api.operations_routes as routes_mod
        resp = flask_client.post(endpoint, data=json.dumps({
            'directory': str(tmp_path), 'includeVideos': True,
        }), content_type='application/json')
        assert resp.status_code == 200
        with routes_mod._operation_lock:
            routes_mod._operation_state['status'] = 'idle'
