"""Email the submitter when a catalog becomes stale, fails validation, or receives an issue.

Notifications cover unreachable catalogs, validation errors, feedback reports,
and removal requests. Each uses `submitter_email` from the entry file so the
registry can contact the submitter even when the catalog is unreachable.

The export uses crawl results rather than entry files and excludes this
email address.

Each email body escapes external text, including catalog titles, issue
titles, and form answers, before inserting it into HTML.
"""

from __future__ import annotations

import calendar
import html
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

from registry.report import log

if TYPE_CHECKING:
    from registry.validators import RuleGroup

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


# Send weekly validation emails on Monday (0 in `datetime.weekday`).
DIGEST_WEEKDAY = 0


@dataclass(frozen=True)
class FailingCatalog:
    """A catalog with validation errors to include in the weekly email.

    `since` holds the published `portolan_registry:stac_invalid_since` timestamp.
    """

    catalog_id: str
    url: str
    groups: Sequence[RuleGroup]
    since: str
    title: str | None = None


def _rule_items(groups: Sequence[RuleGroup]) -> str:
    items = []
    for g in groups:
        noun = "finding" if g.count == 1 else "findings"
        examples = [f"<li><code>{html.escape(e)}</code></li>" for e in g.examples]
        if g.count > len(g.examples):
            examples.append(f"<li>{g.count - len(g.examples)} additional findings</li>")
        items.append(
            f"<li><code>{html.escape(g.tool)}</code> <code>{html.escape(g.rule_id)}</code> "
            f"({g.count} {noun}): {html.escape(g.description)}"
            f"<ul>{''.join(examples)}</ul></li>"
        )
    return f"<ul>{''.join(items)}</ul>"


def _validation_failure_body(catalogs: Sequence[FailingCatalog]) -> str:
    sections = []
    for c in catalogs:
        title, url = html.escape(c.title or c.catalog_id), html.escape(c.url)
        sections.append(
            f"<h3>{title}</h3>"
            f'<p><strong>Catalog URL:</strong> <a href="{url}">{url}</a><br>'
            f"<strong>Failure first detected:</strong> {html.escape(c.since[:10])}</p>"
            f"{_rule_items(c.groups)}"
        )
    lead = (
        "a catalog that you registered"
        if len(catalogs) == 1
        else f"{len(catalogs)} catalogs that you registered"
    )
    day = calendar.day_name[DIGEST_WEEKDAY]
    return f"""
        <h2>Catalog validation failed</h2>
        <p>The nightly registry check found validation errors in {lead}.
        <code>rashid</code> checks the Portolan requirements, and
        <code>stac-node-validator</code> checks the STAC schemas.</p>
        {"".join(sections)}
        <p>These validation errors leave your catalogs listed with status
        <code>valid</code>. In the registry export,
        <code>portolan_registry:stac_valid</code> is <code>false</code>.</p>
        <p>To investigate the errors, run these checks. Replace
        <code>&lt;catalog URL&gt;</code> with your published catalog URL and
        <code>&lt;catalog folder&gt;</code> with the path to your local catalog copy.
        Use a folder for <code>stac-node-validator</code> to include nested metadata.</p>
        <pre>rashid check --no-data --no-live --schema &lt;catalog URL&gt;
npx stac-node-validator --depth -1 &lt;catalog folder&gt;</pre>
        <p>After you fix the errors, publish your updated catalog. The next passing
        nightly check clears the failure timestamp and sets
        <code>portolan_registry:stac_valid</code> to <code>true</code>.
        For help with the errors, see the
        <a href="https://github.com/portolan-sdi/rashid">rashid documentation</a> and
        <a href="https://github.com/m-mohr/stac-node-validator">stac-node-validator documentation</a>.</p>
        <p>Each {day}, the registry sends you one email listing your catalogs that
        fail that check. Catalogs that pass validation no longer appear in the email.</p>
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


def send_validation_failure_notification(
    submitter_email: str | None,
    catalogs: Sequence[FailingCatalog],
    *,
    enabled: bool = True,
) -> bool:
    """Email a submitter the validation errors for their catalogs.

    The nightly run calls this on `DIGEST_WEEKDAY` for catalogs that are not
    deprecated. Grouping all of a submitter's catalogs into one weekly email
    limits repeated notifications and reduces the number of emails sent.
    Return `True` if the email was sent.
    """
    subject = (
        f"[Portolan Registry] Catalog validation failed: "
        f"{catalogs[0].title or catalogs[0].catalog_id}"
        if len(catalogs) == 1
        else f"[Portolan Registry] Validation failed for {len(catalogs)} catalogs"
    )
    return _send(
        ", ".join(c.catalog_id for c in catalogs),
        submitter_email=submitter_email,
        subject=subject,
        body=_validation_failure_body(catalogs),
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
