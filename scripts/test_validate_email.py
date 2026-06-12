"""Tests for email validation utilities."""

import pytest

from validate_email import extract_maintainer_email, validate_maintainer_email


class TestValidateMaintainerEmail:
    """Tests for validate_maintainer_email function."""

    def test_valid_email_passes(self):
        """Valid email with working MX records should pass."""
        result = validate_maintainer_email("test@gmail.com", "test-catalog")
        assert result == "test@gmail.com"

    def test_valid_email_normalized(self):
        """Email domain should be normalized to lowercase."""
        result = validate_maintainer_email("Test@GMAIL.COM", "test-catalog")
        assert result == "Test@gmail.com"  # local part stays as-is per RFC

    def test_empty_email_raises(self):
        """Empty email should raise ValueError."""
        with pytest.raises(ValueError, match="Empty email"):
            validate_maintainer_email("", "test-catalog")

    def test_none_email_raises(self):
        """None email should raise ValueError."""
        with pytest.raises(ValueError, match="Empty email"):
            validate_maintainer_email(None, "test-catalog")

    def test_invalid_syntax_raises(self):
        """Syntactically invalid email should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid maintainer email"):
            validate_maintainer_email("not-an-email", "test-catalog")

    def test_missing_at_sign_raises(self):
        """Email missing @ should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid maintainer email"):
            validate_maintainer_email("testgmail.com", "test-catalog")

    def test_fake_domain_raises(self):
        """Email with non-existent domain (no MX) should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid maintainer email"):
            validate_maintainer_email(
                "contact@fake-nonexistent-domain-xyz123.com", "test-catalog"
            )

    def test_error_includes_catalog_id(self):
        """Error message should include catalog ID."""
        with pytest.raises(ValueError, match="my-catalog-id"):
            validate_maintainer_email("bad", "my-catalog-id")


class TestExtractMaintainerEmail:
    """Tests for extract_maintainer_email function."""

    def test_none_providers_returns_none(self):
        """None providers should return None."""
        assert extract_maintainer_email(None) is None

    def test_empty_providers_returns_none(self):
        """Empty providers list should return None."""
        assert extract_maintainer_email([]) is None

    def test_provider_with_email_returns_email(self):
        """Provider with contact email should return it."""
        providers = [{"name": "Test Org", "contact": {"email": "test@example.com"}}]
        assert extract_maintainer_email(providers) == "test@example.com"

    def test_first_email_wins(self):
        """Should return first provider's email."""
        providers = [
            {"name": "First", "contact": {"email": "first@example.com"}},
            {"name": "Second", "contact": {"email": "second@example.com"}},
        ]
        assert extract_maintainer_email(providers) == "first@example.com"

    def test_skips_provider_without_contact(self):
        """Should skip providers without contact info."""
        providers = [
            {"name": "No Contact"},
            {"name": "Has Contact", "contact": {"email": "found@example.com"}},
        ]
        assert extract_maintainer_email(providers) == "found@example.com"

    def test_skips_provider_with_empty_contact(self):
        """Should skip providers with empty contact dict."""
        providers = [
            {"name": "Empty", "contact": {}},
            {"name": "Has Email", "contact": {"email": "found@example.com"}},
        ]
        assert extract_maintainer_email(providers) == "found@example.com"

    def test_skips_non_dict_providers(self):
        """Should handle non-dict items in providers list."""
        providers = ["string", None, {"contact": {"email": "found@example.com"}}]
        assert extract_maintainer_email(providers) == "found@example.com"

    def test_contact_without_email_returns_none(self):
        """Contact without email key should return None."""
        providers = [{"name": "Test", "contact": {"phone": "123-456"}}]
        assert extract_maintainer_email(providers) is None
