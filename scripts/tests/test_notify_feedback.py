"""Turning a feedback issue into one mail, or into one comment.

The body arrives as GitHub rendered it from the issue form, or as an agent
composed it with the same headings. Both shapes are exercised here. The mail
itself is covered in test_notify.py, so the sender is a spy: what is under
test is which catalog the script resolves, and what it says when it cannot.
"""

from __future__ import annotations

import json

import notify_feedback
import pytest
from notify_feedback import parse_catalog_id, parse_sections

# What GitHub renders from .github/ISSUE_TEMPLATE/catalog-feedback.yml.
RENDERED = """### Catalog ID

pergamino-ide

### Kind of problem

Data quality

### What you found

Every collection declares `proprietary`.

### How you hit it

```shell
$ curl -fsSL https://data.source.coop/nlebovits/pergamino-ide/catalog.json
178
```

### Date observed

2026-08-11

### Tool or agent

_No response_"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A catalogs/ directory with one entry, and an export naming it."""
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    (catalogs / "pergamino-ide.yaml").write_text(
        "url: https://data.source.coop/nlebovits/pergamino-ide/catalog.json\n"
        "submitter_email: you@example.org\n"
    )
    export = tmp_path / "catalogs.json"
    export.write_text(
        json.dumps(
            {
                "links": [
                    {
                        "rel": "child",
                        "title": "Pergamino IDE",
                        "portolan_registry:id": "pergamino-ide",
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("ISSUE_BODY", RENDERED)
    monkeypatch.setenv("ISSUE_URL", "https://github.com/o/r/issues/9")
    monkeypatch.setenv("ISSUE_TITLE", "Licenses are wrong")
    return catalogs, export


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notify_feedback,
        "send_feedback_notification",
        lambda catalog_id, **kw: calls.append({"id": catalog_id, **kw}) or True,
    )
    return calls


def run(registry, extra=()):
    catalogs, export = registry
    return notify_feedback.main(
        ["--catalog-dir", str(catalogs), "--export", str(export), *extra]
    )


class TestParseSections:
    def test_reads_a_rendered_form(self):
        sections = parse_sections(RENDERED)
        assert sections["catalog id"] == "pergamino-ide"
        assert sections["kind of problem"] == "Data quality"

    def test_drops_a_blank_answer(self):
        assert "tool or agent" not in parse_sections(RENDERED)

    def test_keeps_the_fenced_block_intact(self):
        assert "curl -fsSL" in parse_sections(RENDERED)["how you hit it"]

    def test_a_body_with_no_headings_has_no_sections(self):
        assert parse_sections("just some prose") == {}


class TestParseCatalogId:
    def test_from_a_rendered_form(self):
        assert parse_catalog_id(RENDERED) == "pergamino-ide"

    @pytest.mark.parametrize(
        "value", ["`pergamino-ide`", "  pergamino-ide  ", "pergamino-ide\ntrailing"]
    )
    def test_unwraps_what_an_agent_writes(self, value):
        assert parse_catalog_id(f"### Catalog ID\n\n{value}\n") == "pergamino-ide"

    def test_a_missing_section_is_none(self):
        assert parse_catalog_id("### Kind of problem\n\nSchema\n") is None

    @pytest.mark.parametrize(
        "value", ["../../etc/passwd", "a/b", "with space", "semi;colon"]
    )
    def test_anything_that_is_not_a_file_stem_is_none(self, value):
        assert parse_catalog_id(f"### Catalog ID\n\n{value}\n") is None


class TestResolvedCatalog:
    def test_mails_the_submitter(self, registry, sent, capsys):
        assert run(registry) == 0
        assert sent[0]["id"] == "pergamino-ide"
        assert sent[0]["submitter_email"] == "you@example.org"
        assert sent[0]["kind"] == "Data quality"
        assert sent[0]["issue_url"] == "https://github.com/o/r/issues/9"

    def test_titles_the_mail_from_the_export(self, registry, sent):
        run(registry)
        assert sent[0]["title"] == "Pergamino IDE"

    def test_says_nothing_on_the_issue(self, registry, sent, capsys):
        run(registry)
        assert capsys.readouterr().out == ""

    def test_no_notify_still_resolves(self, registry, sent):
        run(registry, extra=["--no-notify"])
        assert sent[0]["enabled"] is False

    def test_a_missing_export_leaves_the_title_unset(self, registry, sent):
        catalogs, export = registry
        export.unlink()
        run(registry)
        assert sent[0]["title"] is None


class TestUnresolvedCatalog:
    def test_an_unregistered_id_comments_instead(
        self, registry, sent, monkeypatch, capsys
    ):
        monkeypatch.setenv("ISSUE_BODY", "### Catalog ID\n\nnot-registered\n")
        assert run(registry) == 0
        assert sent == []
        out = capsys.readouterr().out
        assert "`not-registered`" in out
        assert "`pergamino-ide`" in out

    def test_a_body_naming_nothing_comments(self, registry, sent, monkeypatch, capsys):
        monkeypatch.setenv("ISSUE_BODY", "I found a problem somewhere")
        assert run(registry) == 0
        assert sent == []
        assert "no catalog" in capsys.readouterr().out

    def test_a_traversal_attempt_comments(self, registry, sent, monkeypatch, capsys):
        monkeypatch.setenv("ISSUE_BODY", "### Catalog ID\n\n../../etc/passwd\n")
        assert run(registry) == 0
        assert sent == []
        assert "no catalog" in capsys.readouterr().out


class TestUnreachableSubmitter:
    def test_an_entry_with_no_address_sends_and_says_nothing(
        self, registry, sent, capsys
    ):
        catalogs, _ = registry
        (catalogs / "pergamino-ide.yaml").write_text("url: https://ex.org/catalog.json\n")
        assert run(registry) == 0
        assert sent == []
        assert capsys.readouterr().out == ""

    def test_an_unparseable_address_is_not_the_reporters_problem(
        self, registry, sent, capsys
    ):
        catalogs, _ = registry
        (catalogs / "pergamino-ide.yaml").write_text(
            "url: https://ex.org/catalog.json\nsubmitter_email: not-an-address\n"
        )
        assert run(registry) == 0
        assert sent == []
        assert capsys.readouterr().out == ""
