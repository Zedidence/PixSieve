"""
Drive-aware worker counts through the web API: request schemas accept
`workers: null` (auto), routes resolve the count on the background thread,
and the decision is reported in the operation status.
"""

import json
import threading
import time

import pytest

import pixsieve.api.operations_routes as routes_mod
from pixsieve import config
from pixsieve.api.schemas import MoveRequest, RepairRequest, ScanRequest
from pixsieve.utils import disk_type
from pixsieve.utils.disk_type import Bus, DriveProfile, Media


@pytest.fixture(autouse=True)
def _reset_operation_state():
    yield
    for _ in range(50):
        if routes_mod._operation_state['status'] != 'running':
            break
        time.sleep(0.05)
    with routes_mod._operation_lock:
        routes_mod._operation_state['status'] = 'idle'
        routes_mod._operation_state['workers'] = None


def _capture(monkeypatch, name):
    """Replace an operation function in the routes module with a recorder."""
    captured = {}
    done = threading.Event()

    def fake(*args, **kwargs):
        captured['args'] = args
        captured['kwargs'] = kwargs
        done.set()
        return {'ok': True}

    monkeypatch.setattr(routes_mod, name, fake)
    return captured, done


def _wait_complete():
    for _ in range(100):
        if routes_mod._operation_state['status'] == 'complete':
            return
        time.sleep(0.02)
    raise AssertionError(f"operation did not complete: {routes_mod._operation_state}")


class TestSchemas:
    def test_workers_default_to_auto(self):
        assert ScanRequest(directories=[{'path': '/x'}]).workers is None
        assert MoveRequest(directory='/x', destination='/y').workers is None
        assert RepairRequest(directory='/x', trashFolder='/t').workers is None

    def test_explicit_workers(self):
        assert MoveRequest(directory='/x', destination='/y', workers=8).workers == 8

    @pytest.mark.parametrize('value', [0, 33])
    def test_out_of_range_rejected(self, value):
        with pytest.raises(Exception):
            MoveRequest(directory='/x', destination='/y', workers=value)

    def test_repair_upper_bound(self):
        with pytest.raises(Exception):
            RepairRequest(directory='/x', trashFolder='/t', workers=17)


class TestRoutes:
    def test_move_auto_off_uses_fixed_default(self, flask_client, temp_dir, monkeypatch):
        captured, done = _capture(monkeypatch, 'move_with_structure')
        resp = flask_client.post('/api/operations/move', data=json.dumps({
            'directory': str(temp_dir), 'destination': str(temp_dir / 'out'),
        }), content_type='application/json')
        assert resp.status_code == 200
        assert done.wait(5)
        assert captured['kwargs']['max_workers'] == config.DEFAULT_API_WORKERS
        assert captured['kwargs']['tuner'] is None

    def test_move_explicit_workers(self, flask_client, temp_dir, monkeypatch):
        captured, done = _capture(monkeypatch, 'move_with_structure')
        flask_client.post('/api/operations/move', data=json.dumps({
            'directory': str(temp_dir), 'destination': str(temp_dir / 'out'), 'workers': 3,
        }), content_type='application/json')
        assert done.wait(5)
        assert captured['kwargs']['max_workers'] == 3

    def test_move_to_parent_auto_on_follows_drive(self, flask_client, temp_dir, monkeypatch):
        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        monkeypatch.setattr(disk_type, 'detect_drive',
                            lambda p: DriveProfile('nvme', Media.SSD, Bus.NVME))
        captured, done = _capture(monkeypatch, 'move_to_parent')
        flask_client.post('/api/operations/move-to-parent', data=json.dumps({
            'directory': str(temp_dir),
        }), content_type='application/json')
        assert done.wait(5)
        assert captured['kwargs']['max_workers'] == 16
        _wait_complete()

        status = flask_client.get('/api/operations/status').get_json()
        assert status['workers']['workers'] == 16
        assert status['workers']['label'] == 'NVMe SSD'
        assert status['workers']['source'] == 'auto'

    def test_rename_random_passes_workers_kwarg(self, flask_client, temp_dir, monkeypatch):
        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        monkeypatch.setattr(disk_type, 'detect_drive',
                            lambda p: DriveProfile('usb', Media.HDD, Bus.USB))
        captured, done = _capture(monkeypatch, 'rename_random')
        flask_client.post('/api/operations/rename/random', data=json.dumps({
            'directory': str(temp_dir),
        }), content_type='application/json')
        assert done.wait(5)
        assert captured['kwargs']['workers'] == 2
        assert 'max_workers' not in captured['kwargs']

    def test_repair_is_drive_aware(self, flask_client, temp_dir, monkeypatch):
        monkeypatch.setattr(config, 'AUTO_WORKERS', True)
        monkeypatch.setattr(disk_type, 'detect_drive',
                            lambda p: DriveProfile('sd', Media.SSD, Bus.SD))
        captured, done = _capture(monkeypatch, 'scan_and_repair')
        flask_client.post('/api/operations/repair', data=json.dumps({
            'directory': str(temp_dir), 'trashFolder': str(temp_dir / 'trash'),
        }), content_type='application/json')
        assert done.wait(5)
        assert captured['kwargs']['max_workers'] <= 2

    def test_pipeline_forwards_explicit_workers(self, flask_client, temp_dir, monkeypatch):
        captured, done = _capture(monkeypatch, 'run_pipeline')
        flask_client.post('/api/operations/pipeline', data=json.dumps({
            'directory': str(temp_dir), 'steps': ['cleanup_empty'], 'workers': 5,
        }), content_type='application/json')
        assert done.wait(5)
        assert captured['kwargs']['workers'] == 5

    def test_status_workers_reset_between_operations(self, flask_client, temp_dir, monkeypatch):
        with routes_mod._operation_lock:
            routes_mod._operation_state['workers'] = {'stale': True}
        captured, done = _capture(monkeypatch, 'delete_empty_folders')
        flask_client.post('/api/operations/cleanup', data=json.dumps({
            'directory': str(temp_dir),
        }), content_type='application/json')
        assert done.wait(5)
        _wait_complete()
        assert flask_client.get('/api/operations/status').get_json()['workers'] is None
