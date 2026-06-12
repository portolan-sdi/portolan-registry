"""Email validation utilities for catalog maintainer contacts."""

from email_validator import EmailNotValidError, validate_email


def validate_maintainer_email(email: str, catalog_id: str) -> str:
    """Validate maintainer email with full deliverability check.

    Args:
        email: Email address to validate
        catalog_id: Catalog identifier for error messages

    Returns:
        Normalized email address

    Raises:
        ValueError: If email is syntactically invalid or domain has no MX records
    """
    if not email:
        raise ValueError(f"Empty email for catalog {catalog_id}")

    try:
        result = validate_email(email, check_deliverability=True)
        return result.normalized
    except EmailNotValidError as e:
        raise ValueError(f"Invalid maintainer email for {catalog_id}: {email} - {e}")


def extract_maintainer_email(providers: list | None) -> str | None:
    """Extract first maintainer email from STAC providers list.

    Args:
        providers: List of STAC provider objects

    Returns:
        Email address if found, None otherwise
    """
    if not providers:
        return None

    for provider in providers:
        if isinstance(provider, dict):
            contact = provider.get("contact") or {}
            if isinstance(contact, dict) and contact.get("email"):
                return contact["email"]

    return None
