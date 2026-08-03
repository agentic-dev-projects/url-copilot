"""Unit tests for service.services.url_service (pure logic, no DB)."""

import pytest

from service.services.url_service import build_short_url, is_valid_url


class TestIsValidUrl:
    def test_valid_https(self):
        assert is_valid_url("https://example.com") is True

    def test_valid_http_with_path(self):
        assert is_valid_url("http://example.com/some/path?q=1") is True

    def test_missing_scheme(self):
        assert is_valid_url("example.com") is False

    def test_ftp_scheme_rejected(self):
        assert is_valid_url("ftp://example.com") is False

    def test_empty_string(self):
        assert is_valid_url("") is False

    def test_no_host(self):
        assert is_valid_url("https://") is False


class TestBuildShortUrl:
    def test_combines_base_url_and_code(self):
        result = build_short_url("abc123")
        assert result.endswith("/abc123")

    def test_no_double_slash(self):
        result = build_short_url("abc123")
        assert "//" not in result.replace("://", "")
