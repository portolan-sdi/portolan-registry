#!/usr/bin/env python3
"""Mail the submitter of a catalog someone filed feedback against.

Reads the issue from the environment, so nothing an issue author writes ever
reaches a shell. Resolves the catalog id it names against `catalogs/`, and
mails the address on that entry file.

Whatever it cannot resolve it says on the issue instead: the script prints a
comment to stdout, and the workflow posts it when the output is non-empty.
Progress goes to stderr. The exit code is always 0, because a report that
names the wrong catalog is still a report worth keeping.

    ISSUE_BODY="$(cat body.md)" uv run --frozen scripts/notify_feedback.py
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from registry.contacts import validate_submitter_email
from registry.entries import CATALOG_DIR, entry_paths, load_entry
from registry.export import EXPORT_PATH, load_links
from registry.notify import send_feedback_notification
from registry.report import log

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")

# GitHub writes this into a section the author left blank.
NO_RESPONSE = "_No response_"

# A registry id is a file stem under catalogs/. Anything else is not an id,
# and refusing it here is what keeps `catalogs/{id}.yaml` inside the
# directory.
ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

CATALOG_FIELD = "catalog id"
KIND_FIELD = "kind of problem"


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


def unknown_catalog_comment(catalog_id: str | None, known: list[str]) -> str:
    """What to say on an issue whose catalog we could not resolve."""
    named = f"`{catalog_id}`" if catalog_id else "no catalog"
    ids = ", ".join(f"`{i}`" for i in known) or "none yet"
    return (
        f"This report names {named}, which is not registered here, so nobody "
        "was notified.\n\n"
        f"Registered ids: {ids}\n\n"
        "Correct the **Catalog ID** section, then a maintainer can re-apply "
        "the `catalog-feedback` label to send the notification. If the "
        "catalog is not in the registry at all, "
        "[register it first](https://github.com/portolan-sdi/portolan-registry"
        "#submit-a-catalog)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", default=str(CATALOG_DIR))
    parser.add_argument("--export", default=str(EXPORT_PATH))
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Never send submitter email. Use for manual and test runs.",
    )
    args = parser.parse_args(argv)

    body = os.environ.get("ISSUE_BODY", "")
    issue_url = os.environ.get("ISSUE_URL", "")
    issue_title = os.environ.get("ISSUE_TITLE", "")

    catalog_dir = Path(args.catalog_dir)
    catalog_id = parse_catalog_id(body)
    known = [p.stem for p in entry_paths(catalog_dir)]

    if catalog_id is None or catalog_id not in known:
        log(f"No registered catalog for {catalog_id!r}; commenting instead")
        print(unknown_catalog_comment(catalog_id, known))
        return 0

    log(f"=== Feedback on {catalog_id} ===")
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

    send_feedback_notification(
        catalog_id,
        submitter_email=address,
        issue_url=issue_url,
        issue_title=issue_title,
        kind=parse_sections(body).get(KIND_FIELD, "unspecified"),
        title=link.get("title"),
        enabled=not args.no_notify,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
