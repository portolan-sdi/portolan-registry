"""Maintainer contact details from STAC providers.

One implementation, two call-site policies: validate_entries treats an
invalid address as a hard error, revalidate_all catches and logs it.
"""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email


def validate_maintainer_email(
    email: str | None,
    catalog_id: str,
    *,
    check_deliverability: bool = True,
) -> str:
    """Return the normalized address, or raise ValueError.

    Set `check_deliverability=False` to check syntax only, which avoids a
    DNS lookup.
    """
    if not email:
        raise ValueError(f"Empty email for catalog {catalog_id}")

    try:
        result = validate_email(email, check_deliverability=check_deliverability)
    except EmailNotValidError as e:
        raise ValueError(
            f"Invalid maintainer email for {catalog_id}: {email} - {e}"
        ) from e
    return result.normalized


def extract_maintainer_email(providers: list | None) -> str | None:
    """First provider contact email, or None."""
    if not providers:
        return None
    for provider in providers:
        if isinstance(provider, dict):
            contact = provider.get("contact") or {}
            if isinstance(contact, dict) and contact.get("email"):
                return contact["email"]
    return None
