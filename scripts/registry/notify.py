"""Mail to the submitter of a catalog that has gone stale.

A catalog goes stale when its root catalog.json could not be fetched, so at
the moment we want to send mail the catalog itself is unreadable. Any address
inside it is out of reach precisely when we need it. The registry therefore
keeps its own copy: `submitter_email` on the entry file, which stays readable
whatever happens to the catalog.

The export must never carry that address. It is built from crawl results
rather than from entry files, so the address has no path into it.
"""

from __future__ import annotations

import os

import requests

from registry.report import log

RESEND_ENDPOINT = "https://api.resend.com/emails"
FROM_ADDRESS = "Portolan Registry <registry@portolan-sdi.org>"


def _body(title: str, url: str, failure_reason: str) -> str:
    return f"""
        <h2>Catalog Validation Failed</h2>
        <p>Your STAC catalog <strong>{title}</strong> failed validation during our periodic re-check.</p>
        <p><strong>Catalog URL:</strong> <a href="{url}">{url}</a></p>
        <p><strong>Reason:</strong> {failure_reason}</p>
        <p>If the catalog remains unreachable for 30 days, it will be marked as removed from the registry.</p>
        <p>To resolve this issue, please ensure your catalog is accessible and re-submit if needed.</p>
        <hr>
        <p><small>This is an automated message from the <a href="https://portolan-sdi.org">Portolan Registry</a>.</small></p>
    """


def send_stale_notification(
    catalog_id: str,
    *,
    submitter_email: str | None,
    url: str,
    failure_reason: str,
    title: str | None = None,
    enabled: bool = True,
) -> bool:
    """Mail the submitter that their catalog went stale. Returns True if sent.

    Never raises: a notification failure must not abort a re-validation run.
    """
    if not enabled:
        log(f"  Skipping notification for {catalog_id}: notifications disabled")
        return False

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        log(f"  Skipping notification for {catalog_id}: RESEND_API_KEY not set")
        return False

    # Entries added before the address became required carry none. Those
    # catalogs still re-validate and still go stale; they just go unreported.
    if not submitter_email:
        log(f"  Skipping notification for {catalog_id}: no submitter address on file")
        return False

    try:
        resp = requests.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": FROM_ADDRESS,
                "to": [submitter_email],
                "subject": (
                    "[Portolan Registry] Catalog validation failed: "
                    f"{title or catalog_id}"
                ),
                "html": _body(title or catalog_id, url, failure_reason),
            },
            timeout=30,
        )
    except Exception as e:
        log(f"  Error sending notification for {catalog_id}: {e}")
        return False

    if resp.status_code == 200:
        log(f"  Notification sent to {submitter_email}")
        return True
    log(f"  Failed to send notification: {resp.status_code} {resp.text}")
    return False
