"""
Unit tests for pixsieve/app.py's create_app() configuration.
"""

from pixsieve.app import create_app, LOG_QUIET


def test_secret_key_is_random_per_process():
    """secret_key must not be a fixed, source-controlled string."""
    app1 = create_app(log_level=LOG_QUIET)
    app2 = create_app(log_level=LOG_QUIET)
    assert app1.secret_key != 'duplicate-finder-secret-key'
    assert app1.secret_key != app2.secret_key


def test_oversized_request_returns_413():
    """A request body over MAX_CONTENT_LENGTH is rejected before it reaches a route."""
    app = create_app(log_level=LOG_QUIET)
    app.config['TESTING'] = True
    app.config['MAX_CONTENT_LENGTH'] = 100  # shrink so the test body stays tiny
    with app.test_client() as client:
        resp = client.post(
            '/api/operations/cleanup',
            data=b'{"directory": "' + b'x' * 200 + b'"}',
            content_type='application/json',
        )
        assert resp.status_code == 413
