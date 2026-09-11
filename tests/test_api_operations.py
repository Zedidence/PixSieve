"""
Unit tests for pixsieve/api/operations_routes.py Flask endpoints.
"""

import pytest
import json
import threading
import time
import tempfile
from pathlib import Path
from PIL import Image


@pytest.fixture(autouse=True)
def _reset_operation_state():
    """
    Force the shared _operation_state back to idle after every test in this
    file. Most tests here only assert on the immediate HTTP response and
    don't wait for their (real, if fast) background thread to finish -- if
    that thread is still running when the next test starts a new operation,
    the concurrency guard added to _run_operation would spuriously 409 it.
    """
    import pixsieve.api.operations_routes as routes_mod

    yield
    with routes_mod._operation_lock:
        routes_mod._operation_state['status'] = 'idle'


class TestOperationsStatus:
    """Test GET /api/operations/status endpoint."""

    def test_returns_idle_state(self, flask_client):
        """Status endpoint returns idle state by default."""
        resp = flask_client.get('/api/operations/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] in ('idle', 'running', 'complete', 'error')


class TestOperationsAvailable:
    """Test GET /api/operations/available endpoint."""

    def test_returns_pipeline_steps(self, flask_client):
        """Available endpoint returns pipeline step definitions."""
        resp = flask_client.get('/api/operations/available')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'pipeline_steps' in data
        steps = data['pipeline_steps']
        assert 'random_rename' in steps
        assert 'cleanup_empty' in steps


class TestApiValidation:
    """Test input validation shared across POST endpoints."""

    ENDPOINTS = [
        '/api/operations/move-to-parent',
        '/api/operations/rename/random',
        '/api/operations/rename/parent',
        '/api/operations/sort/alpha',
        '/api/operations/fix-extensions',
        '/api/operations/convert',
        '/api/operations/cleanup',
        '/api/operations/metadata/strip-ratings',
    ]

    def test_missing_directory_returns_400(self, flask_client):
        """Missing directory returns 400 for all endpoints."""
        for endpoint in self.ENDPOINTS:
            resp = flask_client.post(
                endpoint,
                data=json.dumps({}),
                content_type='application/json',
            )
            assert resp.status_code == 400, f"Failed for {endpoint}"

    def test_empty_directory_returns_400(self, flask_client):
        """Empty directory string returns 400."""
        for endpoint in self.ENDPOINTS:
            resp = flask_client.post(
                endpoint,
                data=json.dumps({'directory': ''}),
                content_type='application/json',
            )
            assert resp.status_code == 400, f"Failed for {endpoint}"

    def test_relative_path_returns_400(self, flask_client):
        """Relative path returns 400."""
        resp = flask_client.post(
            '/api/operations/cleanup',
            data=json.dumps({'directory': 'relative/path'}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_nonexistent_directory_returns_400(self, flask_client):
        """Non-existent directory returns 400."""
        resp = flask_client.post(
            '/api/operations/cleanup',
            data=json.dumps({'directory': '/nonexistent/path/that/does/not/exist'}),
            content_type='application/json',
        )
        assert resp.status_code == 400


class TestApiCleanup:
    """Test POST /api/operations/cleanup endpoint."""

    def test_valid_request_starts_operation(self, flask_client):
        """Valid request starts the cleanup operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/cleanup',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['status'] == 'started'
            assert data['operation'] == 'cleanup'


class TestApiStripRatings:
    """Test POST /api/operations/metadata/strip-ratings endpoint."""

    def test_missing_exiftool_returns_400(self, flask_client):
        """A valid directory but no exiftool on PATH returns 400, not a started job."""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                'pixsieve.api.operations_routes.check_exiftool_available',
                lambda: (False, 'exiftool not found'),
            )
            resp = flask_client.post(
                '/api/operations/metadata/strip-ratings',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 400
            assert 'exiftool' in resp.get_json()['error']

    def test_valid_request_starts_operation(self, flask_client):
        """Valid request with exiftool available starts the operation."""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                'pixsieve.api.operations_routes.check_exiftool_available',
                lambda: (True, ''),
            )
            resp = flask_client.post(
                '/api/operations/metadata/strip-ratings',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['status'] == 'started'
            assert data['operation'] == 'strip-ratings'


class TestApiRenameImage:
    """Test POST /api/image/rename endpoint."""

    def _rename(self, flask_client, path, new_name, scan_dir):
        from pixsieve.state import scan_state
        original_directory = scan_state.directory
        original_directories = scan_state.directories
        scan_state.directory = scan_dir
        scan_state.directories = [{'path': scan_dir, 'is_reference': False}]
        try:
            return flask_client.post(
                '/api/image/rename',
                data=json.dumps({'path': path, 'newName': new_name}),
                content_type='application/json',
            )
        finally:
            scan_state.directory = original_directory
            scan_state.directories = original_directories

    def test_no_active_scan_returns_403(self, flask_client):
        """Renaming with no active scan directory set is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'a.jpg'
            src.write_bytes(b'fake')
            resp = flask_client.post(
                '/api/image/rename',
                data=json.dumps({'path': str(src), 'newName': 'b.jpg'}),
                content_type='application/json',
            )
            assert resp.status_code == 403

    def test_happy_path_renames_file(self, flask_client):
        """A valid rename within the scanned directory succeeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'a.jpg'
            src.write_bytes(b'fake')
            resp = self._rename(flask_client, str(src), 'b.jpg', tmpdir)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['status'] == 'renamed'
            assert data['filename'] == 'b.jpg'
            assert not src.exists()
            assert (Path(tmpdir) / 'b.jpg').exists()

    def test_path_outside_scan_directory_rejected(self, flask_client):
        """A file outside the active scan directory is rejected (path traversal guard)."""
        with tempfile.TemporaryDirectory() as scan_dir, tempfile.TemporaryDirectory() as other_dir:
            outside = Path(other_dir) / 'a.jpg'
            outside.write_bytes(b'fake')
            resp = self._rename(flask_client, str(outside), 'b.jpg', scan_dir)
            assert resp.status_code == 403
            assert outside.exists()

    def test_collision_returns_409(self, flask_client):
        """Renaming to a name that already exists is rejected, not overwritten."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'a.jpg'
            src.write_bytes(b'fake-a')
            dest = Path(tmpdir) / 'b.jpg'
            dest.write_bytes(b'fake-b')
            resp = self._rename(flask_client, str(src), 'b.jpg', tmpdir)
            assert resp.status_code == 409
            assert src.exists()
            assert dest.read_bytes() == b'fake-b'  # untouched

    def test_case_only_rename_succeeds(self, flask_client):
        """A case-only rename on a case-insensitive filesystem is not treated as a collision."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'a.jpg'
            src.write_bytes(b'fake')
            resp = self._rename(flask_client, str(src), 'A.jpg', tmpdir)
            assert resp.status_code == 200
            assert (Path(tmpdir) / 'A.jpg').exists()

    def test_invalid_characters_rejected(self, flask_client):
        """A newName containing a path separator is rejected by schema validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'a.jpg'
            src.write_bytes(b'fake')
            resp = self._rename(flask_client, str(src), '../escape.jpg', tmpdir)
            assert resp.status_code == 400
            assert src.exists()

    def test_missing_file_returns_404(self, flask_client):
        """Renaming a file that doesn't exist on disk returns 404."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / 'missing.jpg'
            resp = self._rename(flask_client, str(missing), 'b.jpg', tmpdir)
            assert resp.status_code == 404

    def test_same_name_returns_400(self, flask_client):
        """Renaming a file to its own current name is rejected as a no-op."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'a.jpg'
            src.write_bytes(b'fake')
            resp = self._rename(flask_client, str(src), 'a.jpg', tmpdir)
            assert resp.status_code == 400
            assert src.exists()

    def test_trailing_dot_stripped_before_use(self, flask_client):
        """A trailing dot/space in newName is stripped so the response matches what's actually created on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / 'a.jpg'
            src.write_bytes(b'fake')
            resp = self._rename(flask_client, str(src), 'b.jpg. ', tmpdir)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['filename'] == 'b.jpg'
            assert data['path'] == str(Path(tmpdir) / 'b.jpg')


class TestIncludeVideosGuard:
    """includeVideos is rejected (400) for operations that don't support
    video files, rather than being silently ignored - see
    pixsieve/operations/capabilities.py and _check_include_videos()."""

    UNSUPPORTED = [
        ('/api/operations/fix-extensions', {}),
        ('/api/operations/convert', {}),
        # repair requires trashFolder at the schema level, so it must be
        # supplied here too -- otherwise the request fails schema validation
        # (missing required field) before ever reaching the video-support
        # check this test means to exercise.
        ('/api/operations/repair', {'trashFolder': '/trash'}),
    ]

    def test_unsupported_ops_reject_include_videos(self, flask_client):
        for endpoint, extra in self.UNSUPPORTED:
            with tempfile.TemporaryDirectory() as tmpdir:
                body = {'directory': tmpdir, 'dryRun': True, 'includeVideos': True}
                body.update(extra)
                resp = flask_client.post(
                    endpoint, data=json.dumps(body), content_type='application/json',
                )
                assert resp.status_code == 400, f"Failed for {endpoint}"
                assert 'video' in resp.get_json()['error']

    def test_supported_op_accepts_include_videos(self, flask_client):
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/move-to-parent',
                data=json.dumps({'directory': tmpdir, 'dryRun': True, 'includeVideos': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200


class TestApiMoveToParent:
    """Test POST /api/operations/move-to-parent endpoint."""

    def test_valid_request(self, flask_client):
        """Valid request starts the operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/move-to-parent',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['status'] == 'started'

    def test_non_list_extensions_rejected_cleanly(self, flask_client):
        """A bare string 'extensions' value (instead of a list) must be
        cleanly rejected by schema validation, not crash or silently iterate
        character-by-character into a nonsensical extension set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/move-to-parent',
                data=json.dumps({'directory': tmpdir, 'dryRun': True, 'extensions': '.jpg'}),
                content_type='application/json',
            )
            assert resp.status_code == 400


class TestApiMove:
    """Test POST /api/operations/move endpoint."""

    def test_missing_destination_returns_400(self, flask_client):
        """Missing destination returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/move',
                data=json.dumps({'directory': tmpdir}),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_relative_destination_returns_400(self, flask_client):
        """Relative destination path returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/move',
                data=json.dumps({
                    'directory': tmpdir,
                    'destination': 'relative/path',
                }),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_valid_request(self, flask_client):
        """Valid request starts the operation."""
        with tempfile.TemporaryDirectory() as src, \
             tempfile.TemporaryDirectory() as dest:
            resp = flask_client.post(
                '/api/operations/move',
                data=json.dumps({
                    'directory': src,
                    'destination': dest,
                    'dryRun': True,
                }),
                content_type='application/json',
            )
            assert resp.status_code == 200


class TestApiRename:
    """Test rename endpoints."""

    def test_rename_random_valid(self, flask_client):
        """Valid rename/random request starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/rename/random',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200

    def test_rename_parent_valid(self, flask_client):
        """Valid rename/parent request starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/rename/parent',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200


class TestApiSort:
    """Test sort endpoints."""

    def test_sort_alpha_valid(self, flask_client):
        """Valid sort/alpha request starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/sort/alpha',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200

    def test_sort_color_unknown_method(self, flask_client):
        """Unknown color sort method returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/sort/color',
                data=json.dumps({
                    'directory': tmpdir,
                    'method': 'unknown_method',
                    'dryRun': True,
                }),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_sort_color_valid_methods(self, flask_client):
        """Valid color sort methods are accepted."""
        for method in ('dominant', 'bw', 'palette', 'analyze'):
            with tempfile.TemporaryDirectory() as tmpdir:
                resp = flask_client.post(
                    '/api/operations/sort/color',
                    data=json.dumps({
                        'directory': tmpdir,
                        'method': method,
                        'dryRun': True,
                    }),
                    content_type='application/json',
                )
                assert resp.status_code == 200, f"Failed for method={method}"

                # Wait for this method's background operation to finish before
                # firing the next one, now that concurrent starts get a 409.
                for _ in range(50):
                    status = flask_client.get('/api/operations/status').get_json()
                    if status['status'] != 'running':
                        break
                    time.sleep(0.1)


class TestApiConvert:
    """Test convert endpoints."""

    def test_convert_valid(self, flask_client):
        """Valid convert request starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/convert',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200

    def test_fix_extensions_valid(self, flask_client):
        """Valid fix-extensions request starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/fix-extensions',
                data=json.dumps({'directory': tmpdir, 'dryRun': True}),
                content_type='application/json',
            )
            assert resp.status_code == 200


class TestApiMetadata:
    """Test metadata endpoints."""

    def test_randomize_dates_missing_dates(self, flask_client):
        """Missing dates return 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-dates',
                data=json.dumps({'directory': tmpdir}),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_randomize_dates_invalid_dates(self, flask_client):
        """Invalid date format returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-dates',
                data=json.dumps({
                    'directory': tmpdir,
                    'startDate': 'bad-date',
                    'endDate': '2023-12-31',
                }),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_randomize_dates_start_after_end(self, flask_client):
        """Start date after end date returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-dates',
                data=json.dumps({
                    'directory': tmpdir,
                    'startDate': '2025-01-01',
                    'endDate': '2020-01-01',
                }),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_randomize_dates_valid(self, flask_client):
        """Valid randomize-dates request starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-dates',
                data=json.dumps({
                    'directory': tmpdir,
                    'startDate': '2020-01-01',
                    'endDate': '2023-12-31',
                    'dryRun': True,
                }),
                content_type='application/json',
            )
            assert resp.status_code == 200

    def test_randomize_dates_valid_with_sync_exif(self, flask_client, monkeypatch):
        """syncExif=True on the merged endpoint is wired through to sync_exif=True.

        This endpoint used to be two separate routes (randomize-exif backed by
        an EXIF-writing function, randomize-dates backed by a filesystem-only
        one) that have since been merged into a single api_randomize_dates()
        which reads `syncExif` from the request body. Runs the background
        operation thread synchronously so the assertion isn't racing it.
        """
        import pixsieve.api.operations_routes as operations_routes

        class _SyncThread:
            def __init__(self, target=None, **kwargs):
                self._target = target

            def start(self):
                self._target()

        monkeypatch.setattr(operations_routes.threading, 'Thread', _SyncThread)

        captured = {}

        def fake_randomize_dates(*args, **kwargs):
            captured.update(kwargs)
            return {'processed': 0}

        monkeypatch.setattr(operations_routes, 'randomize_dates', fake_randomize_dates)

        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-dates',
                data=json.dumps({
                    'directory': tmpdir,
                    'startDate': '2020-01-01',
                    'endDate': '2023-12-31',
                    'dryRun': True,
                    'syncExif': True,
                }),
                content_type='application/json',
            )
            assert resp.status_code == 200

        assert captured.get('sync_exif') is True


class TestApiPipeline:
    """Test POST /api/operations/pipeline endpoint."""

    def test_missing_steps_returns_400(self, flask_client):
        """Missing steps list returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/pipeline',
                data=json.dumps({'directory': tmpdir}),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_empty_steps_returns_400(self, flask_client):
        """Empty steps list returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/pipeline',
                data=json.dumps({'directory': tmpdir, 'steps': []}),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_invalid_step_returns_400(self, flask_client):
        """Invalid step name returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/pipeline',
                data=json.dumps({
                    'directory': tmpdir,
                    'steps': ['fake_step'],
                }),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_date_steps_without_dates(self, flask_client):
        """Date steps without dates return 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/pipeline',
                data=json.dumps({
                    'directory': tmpdir,
                    'steps': ['randomize_dates'],
                }),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_valid_pipeline_starts(self, flask_client):
        """Valid pipeline request starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/pipeline',
                data=json.dumps({
                    'directory': tmpdir,
                    'steps': ['cleanup_empty'],
                    'dryRun': True,
                }),
                content_type='application/json',
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['status'] == 'started'


class TestParseExtensionsHelper:
    """Unit-level coverage for _parse_extensions()'s defensive type check --
    HTTP-reachable callers now get the same rejection one layer earlier, from
    Pydantic's list[str] typing on the relevant schemas, but this helper is
    still called directly and should never misbehave on a bad type."""

    def test_non_list_value_ignored_not_iterated(self):
        from pixsieve.api.operations_routes import _parse_extensions

        assert _parse_extensions('.jpg') is None

    def test_normal_list_still_normalized(self):
        from pixsieve.api.operations_routes import _parse_extensions

        assert _parse_extensions(['jpg', '.png']) == {'.jpg', '.png'}

    def test_none_and_empty_return_none(self):
        from pixsieve.api.operations_routes import _parse_extensions

        assert _parse_extensions(None) is None
        assert _parse_extensions([]) is None


class TestOperationsConcurrencyGuard:
    """
    None of the /api/operations/* routes may start a second operation while
    one is already running -- mirrors the 409 behavior /api/scan and
    /api/delete already have.
    """

    def test_second_concurrent_request_gets_409(self, flask_client, temp_dir):
        import pixsieve.api.operations_routes as routes_mod

        release = threading.Event()
        entered = threading.Event()

        def _blocking_cleanup(directory, dry_run=True):
            entered.set()
            release.wait(timeout=5)
            return {'deleted': 0}

        original = routes_mod.delete_empty_folders
        routes_mod.delete_empty_folders = _blocking_cleanup
        try:
            resp1 = flask_client.post(
                '/api/operations/cleanup',
                data=json.dumps({'directory': str(temp_dir)}),
                content_type='application/json',
            )
            assert resp1.status_code == 200
            assert entered.wait(timeout=5), "background operation never started"

            resp2 = flask_client.post(
                '/api/operations/cleanup',
                data=json.dumps({'directory': str(temp_dir)}),
                content_type='application/json',
            )
            assert resp2.status_code == 409
            assert 'already running' in resp2.get_json()['error'].lower()
        finally:
            release.set()
            for _ in range(50):
                if routes_mod._operation_state['status'] != 'running':
                    break
                time.sleep(0.1)
            with routes_mod._operation_lock:
                routes_mod._operation_state['status'] = 'idle'
            routes_mod.delete_empty_folders = original
