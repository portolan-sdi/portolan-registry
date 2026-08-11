"""The address answerable for a registry entry.

This is a registry concern, not a spec one. The Portolan spec requires a
maintainer contact on each collection's `host` provider, but it accepts a `url`
in place of an `email`, so a conformant catalog can publish no address at all.
The registry needs one regardless: something has to reach whoever submitted an
entry when it stops validating. So the address is a field on the entry file,
supplied at submission, and the two parties need not be the same.

One implementation, two call-site policies: validate_entries treats an invalid
address as a hard error, revalidate_all catches and logs it.
"""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email


def validate_submitter_email(
    email: str | None,
    catalog_id: str,
    *,
    check_deliverability: bool = True,
) -> str:
    """Return the normalized address, or raise ValueError.

    Set `check_deliverability=False` to check syntax only, which avoids the
    DNS lookup.
    """
    if not email:
        raise ValueError(f"Missing submitter_email for catalog {catalog_id}")

    try:
        result = validate_email(email, check_deliverability=check_deliverability)
    except EmailNotValidError as e:
        raise ValueError(
            f"Invalid submitter_email for catalog {catalog_id}: {email} - {e}"
        ) from e
    return result.normalized
