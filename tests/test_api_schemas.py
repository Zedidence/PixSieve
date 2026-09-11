"""
Unit tests for pixsieve/api/schemas.py.
"""

import json

from pydantic import ValidationError

from pixsieve.api.schemas import (
    DateRangeRequest,
    ScanRequest,
    _format_validation_error,
    parse_request,
)


class TestFormatValidationError:
    """The API's `error` field must stay a short, single-line sentence, not
    pydantic's multi-line implementation-leaking str(exc) dump."""

    def test_field_error_is_single_line_and_names_the_field(self):
        try:
            ScanRequest.model_validate({})
        except ValidationError as exc:
            message = _format_validation_error(exc)

        assert '\n' not in message
        assert 'https://errors.pydantic.dev' not in message
        assert 'directories' in message

    def test_model_level_error_has_no_dangling_location_prefix(self):
        try:
            DateRangeRequest.model_validate({
                'directory': '/x', 'startDate': '2024-01-05', 'endDate': '2024-01-01',
            })
        except ValidationError as exc:
            message = _format_validation_error(exc)

        assert '\n' not in message
        assert not message.startswith(': ')
        assert 'before' in message


class TestParseRequest:
    def test_missing_body_returns_clean_error(self, flask_client):
        with flask_client.application.test_request_context():
            model, err = parse_request(ScanRequest, None)
        assert model is None
        response, status = err
        assert status == 400
        assert response.get_json()['error'] == 'Request body required'

    def test_valid_data_returns_model_no_error(self, flask_client):
        with flask_client.application.test_request_context():
            model, err = parse_request(
                ScanRequest, {'directories': [{'path': '/x'}]}
            )
        assert err is None
        assert model.directories[0].path == '/x'


class TestApiSelections:
    """POST /api/selections must reject values other than keep/delete."""

    def test_valid_selection_values_accepted(self, flask_client):
        resp = flask_client.post(
            '/api/selections',
            data=json.dumps({'selections': {'/a.jpg': 'keep', '/b.jpg': 'delete'}}),
            content_type='application/json',
        )
        assert resp.status_code == 200

    def test_invalid_selection_value_rejected(self, flask_client):
        resp = flask_client.post(
            '/api/selections',
            data=json.dumps({'selections': {'/a.jpg': 'banana'}}),
            content_type='application/json',
        )
        assert resp.status_code == 400
