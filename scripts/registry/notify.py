"""Mail to the submitter of a registered catalog.

Two things prompt mail. A catalog goes stale when its root catalog.json could
not be fetched, so at the moment we want to send mail the catalog itself is
unreadable, and any address inside it is out of reach precisely when we need
it. A reader files feedback when the catalog is readable but wrong, which the
crawler cannot detect at all. Both need the same thing: an address the
registry holds itself. That is `submitter_email` on the entry file, which
stays readable whatever happens to the catalog.

The export must never carry that address. It is built from crawl results
rather than from entry files, so the address has no path into it.

Both bodies interpolate strings the registry did not write, a crawled title in
one case and an issue title in the other, so every interpolation is escaped.
"""

from __future__ import annotations

import html
import os

import requests

from registry.report import log

RESEND_ENDPOINT = "https://api.resend.com/emails"
FROM_ADDRESS = "Portolan Registry <registry@portolan-sdi.org>"
FOOTER = (
    "<hr><p><small>This is an automated message from the "
    '<a href="https://portolan-sdi.org">Portolan Registry</a>.</small></p>'
)


def _stale_body(title: str, url: str, failure_reason: str) -> str:
    title, url, failure_reason = (
        html.escape(title),
        html.escape(url),
        html.escape(failure_reason),
    )
    return f"""
        <h2>Catalog Validation Failed</h2>
        <p>Your STAC catalog <strong>{title}</strong> failed validation during our periodic re-check.</p>
        <p><strong>Catalog URL:</strong> <a href="{url}">{url}</a></p>
        <p><strong>Reason:</strong> {failure_reason}</p>
        <p>If the catalog remains unreachable for 30 days, it will be marked as removed from the registry.</p>
        <p>To resolve this issue, please ensure your catalog is accessible and re-submit if needed.</p>
        {FOOTER}
    """


def _feedback_body(title: str, kind: str, issue_url: str, issue_title: str) -> str:
    title, kind, issue_url, issue_title = (
        html.escape(title),
        html.escape(kind),
        html.escape(issue_url),
        html.escape(issue_title),
    )
    return f"""
        <h2>Feedback on Your Catalog</h2>
        <p>Someone reported a problem with <strong>{title}</strong>, which you registered in the Portolan Registry.</p>
        <p><strong>Kind of problem:</strong> {kind}</p>
        <p><strong>Report:</strong> <a href="{issue_url}">{issue_title}</a></p>
        <p>The report is a public issue on the registry. Reply there to reach whoever filed it. Nothing about your registration changes because of this mail.</p>
        {FOOTER}
    """


def _send(
    catalog_id: str,
    *,
    submitter_email: str | None,
    subject: str,
    body: str,
    enabled: bool,
) -> bool:
    """Post one mail to Resend. Returns True if sent.

    Never raises: a notification failure must not abort the run that triggered
    it, whether that is a re-validation sweep or a feedback issue.
    """
    if not enabled:
        log(f"  Skipping notification for {catalog_id}: notifications disabled")
        return False

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        log(f"  Skipping notification for {catalog_id}: RESEND_API_KEY not set")
        return False

    # Entries added before the address became required carry none. Those
    # catalogs still re-validate and still receive feedback; both go
    # unreported.
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
                "subject": subject,
                "html": body,
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


def send_stale_notification(
    catalog_id: str,
    *,
    submitter_email: str | None,
    url: str,
    failure_reason: str,
    title: str | None = None,
    enabled: bool = True,
) -> bool:
    """Mail the submitter that their catalog went stale. Returns True if sent."""
    return _send(
        catalog_id,
        submitter_email=submitter_email,
        subject=(
            f"[Portolan Registry] Catalog validation failed: {title or catalog_id}"
        ),
        body=_stale_body(title or catalog_id, url, failure_reason),
        enabled=enabled,
    )


def send_feedback_notification(
    catalog_id: str,
    *,
    submitter_email: str | None,
    issue_url: str,
    issue_title: str,
    kind: str,
    title: str | None = None,
    enabled: bool = True,
) -> bool:
    """Mail the submitter that someone filed feedback. Returns True if sent."""
    return _send(
        catalog_id,
        submitter_email=submitter_email,
        subject=f"[Portolan Registry] Feedback on your catalog: {title or catalog_id}",
        body=_feedback_body(title or catalog_id, kind, issue_url, issue_title),
        enabled=enabled,
    )
