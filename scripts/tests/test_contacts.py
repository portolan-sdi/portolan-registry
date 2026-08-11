"""Submitter email validation.

Deliverability checks resolve MX records, so the tests that exercise the
library's own DNS work are marked `network` and deselected by default. What
belongs to us is the wrapping of the library's exception into a ValueError
carrying the catalog id, and that is tested with a double.
"""

from __future__ import annotations

import pytest
from email_validator import EmailNotValidError

from registry import contacts
from registry.contacts import validate_submitter_email


class TestValidateSubmitterEmail:
    def test_empty_raises_with_the_catalog_id(self):
        with pytest.raises(ValueError, match="Missing submitter_email for catalog x"):
            validate_submitter_email("", "x")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="Missing submitter_email"):
            validate_submitter_email(None, "my-cat")

    @pytest.mark.parametrize(
        "bad", ["not-an-email", "@example.com", "user@", "user @example.com"]
    )
    def test_syntax_errors_raise_without_dns(self, bad):
        with pytest.raises(ValueError, match="Invalid submitter_email"):
            validate_submitter_email(bad, "my-cat", check_deliverability=False)

    def test_valid_syntax_returns_normalized(self):
        assert (
            validate_submitter_email(
                "User@Example.com", "my-cat", check_deliverability=False
            )
            == "User@example.com"
        )

    def test_library_failure_is_wrapped_with_the_catalog_id(self, monkeypatch):
        def boom(email, check_deliverability=True):
            raise EmailNotValidError("domain has no MX record")

        monkeypatch.setattr(contacts, "validate_email", boom)
        with pytest.raises(ValueError) as exc:
            validate_submitter_email("a@b.example", "argentina")
        assert "argentina" in str(exc.value)
        assert "no MX record" in str(exc.value)

    def test_deliverability_flag_is_passed_through(self, monkeypatch):
        seen = {}

        def spy(email, check_deliverability=True):
            seen["checked"] = check_deliverability
            return type("R", (), {"normalized": email})()

        monkeypatch.setattr(contacts, "validate_email", spy)
        validate_submitter_email("a@b.example", "x", check_deliverability=False)
        assert seen["checked"] is False

    @pytest.mark.network
    def test_real_domain_resolves(self):
        assert validate_submitter_email("test@gmail.com", "x") == "test@gmail.com"

    @pytest.mark.network
    def test_nonexistent_domain_raises(self):
        with pytest.raises(ValueError):
            validate_submitter_email("a@nx-domain-that-does-not-exist.invalid", "x")
