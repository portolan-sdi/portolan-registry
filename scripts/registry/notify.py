"""Email the submitter when a catalog becomes stale or receives an issue.

Notifications cover unreachable catalogs, feedback reports, and removal
requests. Each uses `submitter_email` from the entry file so the registry can
contact the submitter even when the catalog is unreachable.

The export uses crawl results rather than entry files and excludes this
email address.

Each email body escapes external text, including catalog titles, issue
titles, and form answers, before inserting it into HTML.
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


def _stale_body(
    title: str,
    url: str,
    failure_reason: str,
    *,
    deprecated: bool = False,
    superseded_by: str | None = None,
) -> str:
    title, url, failure_reason = (
        html.escape(title),
        html.escape(url),
        html.escape(failure_reason),
    )
    if deprecated:
        successor = (
            f" Its replacement is <code>{html.escape(superseded_by)}</code>."
            if superseded_by
            else ""
        )
        outcome = (
            f"<p>This catalog is deprecated.{successor} Its nightly checks fail if "
            "the registry cannot fetch it or if <code>rashid</code> or "
            "<code>stac-node-validator</code> reports an error. If these checks keep "
            "failing for 30 days, the registry changes its status to <code>removed</code>. "
            "Its entry and export link remain.</p>"
            "<p>To keep the catalog active, fix the reported errors. If you want "
            "these checks to mark it as removed, you do not need to take action.</p>"
        )
    else:
        outcome = (
            "<p>If the catalog remains unreachable for 30 days, the registry changes "
            "its status to <code>removed</code>. Its entry and export link remain.</p>"
            "<p>To keep the catalog active, restore access to its registered URL. "
            "The next successful nightly check clears the failure.</p>"
        )
    return f"""
        <h2>Catalog validation failed</h2>
        <p>Your STAC catalog <strong>{title}</strong> failed its nightly registry check.</p>
        <p><strong>Catalog URL:</strong> <a href="{url}">{url}</a></p>
        <p><strong>Reason:</strong> {failure_reason}</p>
        {outcome}
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


# Allow this many days for the submitter to object to someone else's removal
# request. See the removal policy in the README.
OBJECTION_DAYS = 14


# Map each request action to the noun and verb used in the email.
REQUEST_ACTIONS = {"remove": ("removal", "remove"), "deprecate": ("deprecation", "deprecate")}


def _removal_body(
    title: str,
    requester: str,
    relationship: str,
    reason: str,
    issue_url: str,
    issue_title: str,
    action: str = "remove",
) -> str:
    noun, verb = REQUEST_ACTIONS[action]
    title, requester, relationship, reason, issue_url, issue_title = (
        html.escape(title),
        html.escape(requester),
        html.escape(relationship),
        html.escape(reason),
        html.escape(issue_url),
        html.escape(issue_title),
    )
    return f"""
        <h2>Request to {verb} your catalog</h2>
        <p>GitHub user <strong>{requester}</strong> asked to {verb} <strong>{title}</strong>, which you registered in the Portolan registry.</p>
        <dl>
            <dt>Requester's relationship to the catalog</dt>
            <dd>{relationship}</dd>
            <dt>Reason for the request</dt>
            <dd>{reason}</dd>
            <dt>Request</dt>
            <dd><a href="{issue_url}">{issue_title}</a></dd>
        </dl>
        <p>If you made this request, you do not need to take action. If you object to the {noun}, comment on the issue within {OBJECTION_DAYS} days.</p>
        <p>A maintainer reviews each request before making the change.</p>
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
    deprecated: bool = False,
    superseded_by: str | None = None,
    enabled: bool = True,
) -> bool:
    """Email the submitter when their catalog becomes stale.

    For deprecated catalogs, include the replacement ID, if available, and
    explain that validation errors also count as failed checks. Return
    `True` if the email was sent.
    """
    return _send(
        catalog_id,
        submitter_email=submitter_email,
        subject=(f"[Portolan Registry] Catalog validation failed: {title or catalog_id}"),
        body=_stale_body(
            title or catalog_id,
            url,
            failure_reason,
            deprecated=deprecated,
            superseded_by=superseded_by,
        ),
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


def send_removal_request_notification(
    catalog_id: str,
    *,
    submitter_email: str | None,
    issue_url: str,
    issue_title: str,
    requester: str,
    relationship: str,
    reason: str,
    title: str | None = None,
    action: str = "remove",
    enabled: bool = True,
) -> bool:
    """Email the submitter a removal or deprecation request.

    Use a key from `REQUEST_ACTIONS` for `action`. Return `True` if the email
    was sent.
    """
    if action not in REQUEST_ACTIONS:
        action = "remove"
    return _send(
        catalog_id,
        submitter_email=submitter_email,
        subject=(
            f"[Portolan Registry] Request to {REQUEST_ACTIONS[action][1]} your catalog: "
            f"{title or catalog_id}"
        ),
        body=_removal_body(
            title or catalog_id, requester, relationship, reason, issue_url, issue_title, action
        ),
        enabled=enabled,
    )
