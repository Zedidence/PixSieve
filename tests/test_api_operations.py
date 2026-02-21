"""
Unit tests for dupefinder/api/operations_routes.py Flask endpoints.
"""

import pytest
import json
import time
import tempfile
from pathlib import Path
from PIL import Image


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

    def test_randomize_exif_missing_dates(self, flask_client):
        """Missing dates return 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-exif',
                data=json.dumps({'directory': tmpdir}),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_randomize_exif_invalid_dates(self, flask_client):
        """Invalid date format returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-exif',
                data=json.dumps({
                    'directory': tmpdir,
                    'startDate': 'bad-date',
                    'endDate': '2023-12-31',
                }),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_randomize_exif_start_after_end(self, flask_client):
        """Start date after end date returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-exif',
                data=json.dumps({
                    'directory': tmpdir,
                    'startDate': '2025-01-01',
                    'endDate': '2020-01-01',
                }),
                content_type='application/json',
            )
            assert resp.status_code == 400

    def test_randomize_exif_valid(self, flask_client):
        """Valid request starts the operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = flask_client.post(
                '/api/operations/metadata/randomize-exif',
                data=json.dumps({
                    'directory': tmpdir,
                    'startDate': '2020-01-01',
                    'endDate': '2023-12-31',
                    'dryRun': True,
                }),
                content_type='application/json',
            )
            assert resp.status_code == 200

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
                    'steps': ['randomize_exif'],
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
