"""Maintainer email extraction and validation.

Deliverability checks resolve MX records, so the tests that exercise the
library's own DNS work are marked `network` and deselected by default. What
belongs to us is the wrapping of the library's exception into a ValueError
carrying the catalog id, and that is tested with a double.
"""

from __future__ import annotations

import pytest
from email_validator import EmailNotValidError

from registry import contacts
from registry.contacts import extract_maintainer_email, validate_maintainer_email


class TestValidateMaintainerEmail:
    def test_empty_raises_with_the_catalog_id(self):
        with pytest.raises(ValueError, match="Empty email for catalog my-cat"):
            validate_maintainer_email("", "my-cat")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="Empty email"):
            validate_maintainer_email(None, "my-cat")

    @pytest.mark.parametrize(
        "bad", ["not-an-email", "@example.com", "user@", "user @example.com"]
    )
    def test_syntax_errors_raise_without_dns(self, bad):
        with pytest.raises(ValueError, match="Invalid maintainer email"):
            validate_maintainer_email(bad, "my-cat", check_deliverability=False)

    def test_valid_syntax_returns_normalized(self):
        assert (
            validate_maintainer_email(
                "User@Example.com", "my-cat", check_deliverability=False
            )
            == "User@example.com"
        )

    def test_library_failure_is_wrapped_with_the_catalog_id(self, monkeypatch):
        def boom(email, check_deliverability=True):
            raise EmailNotValidError("domain has no MX record")

        monkeypatch.setattr(contacts, "validate_email", boom)
        with pytest.raises(ValueError) as exc:
            validate_maintainer_email("a@b.example", "argentina")
        assert "argentina" in str(exc.value)
        assert "no MX record" in str(exc.value)

    def test_deliverability_flag_is_passed_through(self, monkeypatch):
        seen = {}

        def spy(email, check_deliverability=True):
            seen["checked"] = check_deliverability
            return type("R", (), {"normalized": email})()

        monkeypatch.setattr(contacts, "validate_email", spy)
        validate_maintainer_email("a@b.example", "x", check_deliverability=False)
        assert seen["checked"] is False

    @pytest.mark.network
    def test_real_domain_resolves(self):
        assert validate_maintainer_email("test@gmail.com", "x") == "test@gmail.com"

    @pytest.mark.network
    def test_nonexistent_domain_raises(self):
        with pytest.raises(ValueError):
            validate_maintainer_email("a@nx-domain-that-does-not-exist.invalid", "x")


class TestExtractMaintainerEmail:
    def test_none_providers(self):
        assert extract_maintainer_email(None) is None

    def test_empty_providers(self):
        assert extract_maintainer_email([]) is None

    def test_first_contact_email_wins(self):
        providers = [
            {"name": "A", "contact": {"email": "first@ex.org"}},
            {"name": "B", "contact": {"email": "second@ex.org"}},
        ]
        assert extract_maintainer_email(providers) == "first@ex.org"

    def test_skips_providers_without_contacts(self):
        providers = [
            {"name": "A"},
            {"name": "B", "contact": {}},
            {"name": "C", "contact": {"email": "found@ex.org"}},
        ]
        assert extract_maintainer_email(providers) == "found@ex.org"

    def test_tolerates_null_contact(self):
        assert extract_maintainer_email([{"name": "A", "contact": None}]) is None

    def test_tolerates_non_dict_entries(self):
        assert extract_maintainer_email(["nope", 42]) is None

    def test_returns_none_when_no_provider_has_an_email(self):
        assert extract_maintainer_email([{"name": "A", "contact": {"url": "x"}}]) is None
