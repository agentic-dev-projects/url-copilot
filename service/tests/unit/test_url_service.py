import pytest
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session
from service.services.url_service import generate_qr_code, get_short_url_by_id
from service.models.short_url import ShortURL
from service.models.user import User


@pytest.fixture
def mock_db_session():
    return Mock(spec=Session)


def test_generate_qr_code():
    url = "http://example.com"
    qr_code = generate_qr_code(url)
    assert qr_code is not None
    assert qr_code.getvalue().startswith(b'\x89PNG\r\n\x1a\n')  # Check if it starts with PNG file signature


def test_get_short_url_by_id_found(mock_db_session):
    mock_user = User(id=1)
    mock_url_id = "123e4567-e89b-12d3-a456-426614174000"
    mock_short_url = ShortURL(id=mock_url_id, owner_id=1, is_active=True)

    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_short_url

    result = get_short_url_by_id(mock_db_session, mock_url_id, mock_user)
    assert result == mock_short_url


def test_get_short_url_by_id_not_found(mock_db_session):
    mock_user = User(id=1)
    mock_url_id = "123e4567-e89b-12d3-a456-426614174000"

    mock_db_session.query.return_value.filter.return_value.first.return_value = None

    result = get_short_url_by_id(mock_db_session, mock_url_id, mock_user)
    assert result is None


@patch('uuid.UUID')
def test_get_short_url_by_id_invalid_uuid(mock_uuid, mock_db_session):
    mock_user = User(id=1)
    mock_uuid.side_effect = ValueError

    result = get_short_url_by_id(mock_db_session, "invalid-uuid", mock_user)
    assert result is None