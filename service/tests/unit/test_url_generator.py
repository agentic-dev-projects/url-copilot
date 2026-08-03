"""Unit tests for service.core.url_generator."""

import re

from service.core.url_generator import generate_short_code, is_valid_custom_alias


class TestGenerateShortCode:
    def test_default_length_is_six(self):
        code = generate_short_code()
        assert len(code) == 6

    def test_custom_length(self):
        code = generate_short_code(length=10)
        assert len(code) == 10

    def test_code_is_url_safe(self):
        for _ in range(100):
            code = generate_short_code()
            assert re.match(r"^[a-zA-Z0-9]+$", code), f"Unsafe chars in: {code}"

    def test_codes_are_unique(self):
        codes = {generate_short_code() for _ in range(200)}
        # Allow at most 1 collision in 200 — astronomically unlikely at 6 chars
        assert len(codes) >= 199


class TestIsValidCustomAlias:
    def test_valid_alias(self):
        assert is_valid_custom_alias("my-campaign") is True

    def test_valid_alphanumeric(self):
        assert is_valid_custom_alias("abc123") is True

    def test_too_short(self):
        assert is_valid_custom_alias("ab") is False

    def test_too_long(self):
        assert is_valid_custom_alias("a" * 33) is False

    def test_starts_with_hyphen(self):
        assert is_valid_custom_alias("-invalid") is False

    def test_ends_with_hyphen(self):
        assert is_valid_custom_alias("invalid-") is False

    def test_special_characters_rejected(self):
        assert is_valid_custom_alias("bad alias!") is False
        assert is_valid_custom_alias("bad_alias") is False

    def test_minimum_valid_length(self):
        assert is_valid_custom_alias("abc") is True

    def test_maximum_valid_length(self):
        assert is_valid_custom_alias("a" + "-" * 28 + "b") is True
