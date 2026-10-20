#!/usr/bin/env python3
"""Email a catalog's submitter about feedback or a removal request.

Use `--form feedback` (the default) for a catalog feedback report or
`--form removal` for a catalog removal request.

The script reads issue details from environment variables, looks up the
catalog ID in `catalogs/`, and emails the address in the entry file. Issue
content never reaches the shell.

If the script cannot find the catalog, it prints a comment to stdout for the
workflow to post on the issue. Progress messages go to stderr. The script
returns exit code 0 even if the catalog ID is invalid.

    ISSUE_BODY="$(cat body.md)" uv run --frozen scripts/notify_feedback.py
    ISSUE_BODY="$(cat body.md)" uv run --frozen scripts/notify_feedback.py --form removal
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from registry.contacts import validate_submitter_email
from registry.entries import CATALOG_DIR, entry_paths, load_entry
from registry.export import EXPORT_PATH, load_links
from registry.notify import send_feedback_notification, send_removal_request_notification
from registry.report import log

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")

# GitHub writes this into a section the author left blank.
NO_RESPONSE = "_No response_"

# A registry id is a file stem under catalogs/. Anything else is not an id,
# and refusing it here is what keeps `catalogs/{id}.yaml` inside the
# directory.
ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

CATALOG_FIELD = "catalog id"
# Two forms apply the catalog-feedback label, and each labels the kind of
# problem in its own words: catalog-feedback.yml and the synced
# dataset-problem.yml.
KIND_FIELDS = ("kind of problem", "what kind of problem is it?")
# The removal request form, .github/ISSUE_TEMPLATE/catalog-removal.yml.
RELATIONSHIP_FIELD = "relationship"
REASON_FIELD = "why"

# Each form applies its own label. Reapply the label to resend the email.
LABELS = {"feedback": "catalog-feedback", "removal": "catalog-removal"}


def parse_sections(body: str) -> dict[str, str]:
    """Map heading -> text beneath it, lowercased headings, blanks dropped.

    An issue form renders each field as a heading followed by the answer, so
    this reads a filled form. It also reads a body an agent composed with the
    same headings, which is how the report-catalog-issue skill files one.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for raw in body.replace("\r\n", "\n").split("\n"):
        heading = HEADING_RE.match(raw)
        if heading:
            current = heading.group(1).strip().casefold()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw)

    out = {}
    for title, lines in sections.items():
        text = "\n".join(lines).strip()
        if text and text != NO_RESPONSE:
            out[title] = text
    return out


def parse_catalog_id(body: str) -> str | None:
    """The registry id the report names, or None when it names nothing usable."""
    value = parse_sections(body).get(CATALOG_FIELD)
    if not value:
        return None
    # One line, unwrapped from the backticks an agent tends to add.
    value = value.split("\n", 1)[0].strip().strip("`").strip()
    return value if ID_RE.match(value) else None


def unknown_catalog_comment(
    catalog_id: str | None, known: list[str], label: str = LABELS["feedback"]
) -> str:
    """Explain how to correct an issue with an unrecognized catalog ID."""
    named = f"`{catalog_id}`" if catalog_id else "no catalog"
    ids = ", ".join(f"`{i}`" for i in known) or "none yet"
    return (
        f"This issue names {named}. The registry could not find a matching "
        "entry and did not send a notification.\n\n"
        f"Registered IDs: {ids}\n\n"
        "If the catalog is registered, correct the **Catalog ID** section "
        f"and ask a maintainer to reapply the `{label}` label to send the "
        "notification. If the catalog is not registered, "
        "[submit the catalog](https://github.com/portolan-sdi/portolan-registry"
        "#quickstart)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", default=str(CATALOG_DIR))
    parser.add_argument("--export", default=str(EXPORT_PATH))
    parser.add_argument(
        "--form",
        choices=sorted(LABELS),
        default="feedback",
        help="Select the issue form to read (default: feedback).",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Never send submitter email. Use for manual and test runs.",
    )
    args = parser.parse_args(argv)

    body = os.environ.get("ISSUE_BODY", "")
    issue_url = os.environ.get("ISSUE_URL", "")
    issue_title = os.environ.get("ISSUE_TITLE", "")
    issue_author = os.environ.get("ISSUE_AUTHOR", "")

    catalog_dir = Path(args.catalog_dir)
    catalog_id = parse_catalog_id(body)
    known = [p.stem for p in entry_paths(catalog_dir)]

    if catalog_id is None or catalog_id not in known:
        log(f"No registered catalog for {catalog_id!r}; commenting instead")
        print(unknown_catalog_comment(catalog_id, known, LABELS[args.form]))
        return 0

    log(f"=== {'Removal request' if args.form == 'removal' else 'Feedback'} on {catalog_id} ===")
    entry = load_entry(catalog_dir / f"{catalog_id}.yaml")

    try:
        address = validate_submitter_email(
            entry.get("submitter_email"), catalog_id, check_deliverability=False
        )
    except ValueError as e:
        # The address is a hard error at submission, so an entry reaching this
        # path predates that rule or was hand-edited. Say nothing on the
        # issue: the reporter cannot fix it and the address is not theirs.
        log(f"  {e}")
        return 0

    link = load_links(Path(args.export)).get(catalog_id, {})
    sections = parse_sections(body)

    if args.form == "removal":
        send_removal_request_notification(
            catalog_id,
            submitter_email=address,
            issue_url=issue_url,
            issue_title=issue_title,
            requester=issue_author or "unknown",
            relationship=sections.get(RELATIONSHIP_FIELD, "not stated"),
            reason=sections.get(REASON_FIELD, "not stated"),
            title=link.get("title"),
            enabled=not args.no_notify,
        )
        return 0

    send_feedback_notification(
        catalog_id,
        submitter_email=address,
        issue_url=issue_url,
        issue_title=issue_title,
        kind=next((sections[f] for f in KIND_FIELDS if f in sections), "unspecified"),
        title=link.get("title"),
        enabled=not args.no_notify,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
