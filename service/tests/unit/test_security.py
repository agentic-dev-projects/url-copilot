"""Unit tests for service.core.security."""

from service.core.security import generate_api_key, hash_api_key, verify_api_key


class TestGenerateApiKey:
    def test_returns_three_values(self):
        result = generate_api_key()
        assert len(result) == 3

    def test_raw_key_has_sk_prefix(self):
        raw_key, _, _ = generate_api_key()
        assert raw_key.startswith("sk_")

    def test_key_prefix_matches_raw_key(self):
        raw_key, _, key_prefix = generate_api_key()
        assert raw_key.startswith(key_prefix)

    def test_hash_is_64_hex_chars(self):
        _, key_hash, _ = generate_api_key()
        assert len(key_hash) == 64
        assert all(c in "0123456789abcdef" for c in key_hash)

    def test_two_keys_are_unique(self):
        key1, _, _ = generate_api_key()
        key2, _, _ = generate_api_key()
        assert key1 != key2


class TestVerifyApiKey:
    def test_correct_key_returns_true(self):
        raw_key, key_hash, _ = generate_api_key()
        assert verify_api_key(raw_key, key_hash) is True

    def test_wrong_key_returns_false(self):
        _, key_hash, _ = generate_api_key()
        assert verify_api_key("sk_wrongkey", key_hash) is False

    def test_empty_key_returns_false(self):
        _, key_hash, _ = generate_api_key()
        assert verify_api_key("", key_hash) is False


class TestHashApiKey:
    def test_same_input_produces_same_hash(self):
        raw_key, _, _ = generate_api_key()
        assert hash_api_key(raw_key) == hash_api_key(raw_key)

    def test_different_inputs_produce_different_hashes(self):
        k1, _, _ = generate_api_key()
        k2, _, _ = generate_api_key()
        assert hash_api_key(k1) != hash_api_key(k2)
