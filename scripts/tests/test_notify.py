"""Mail to submitters: what gets sent, and when nothing does.

Resend is replaced with a recorder. What belongs to us is the payload, the
three conditions under which we decline to send, and the promise that a
failure here never propagates: every sender is called from a run that must
finish regardless.
"""

from __future__ import annotations

import pytest

from registry import notify
from registry.validators import RuleGroup


class Recorder:
    """Stands in for requests.post."""

    def __init__(self, status_code: int = 200, raises: Exception | None = None):
        self.status_code = status_code
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self.raises:
            raise self.raises
        return type("Resp", (), {"status_code": self.status_code, "text": "boom"})()


@pytest.fixture
def post(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(notify.requests, "post", recorder)
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    return recorder


def stale(**kwargs):
    args = {
        "submitter_email": "you@example.org",
        "url": "https://ex.org/catalog.json",
        "failure_reason": "404 Not Found",
        "title": "Example Catalog",
    }
    return notify.send_stale_notification("example", **{**args, **kwargs})


def failing(catalog_id="example", title="Example Catalog"):
    group = RuleGroup("rashid", "PTL-PRV-002", "a mirror needs a via link", 22, ["c.json: m"])
    return notify.FailingCatalog(
        catalog_id, "https://ex.org/catalog.json", [group], "2026-10-19T06:00:00+00:00", title
    )


def validation_failure(*catalogs, submitter_email="you@example.org"):
    return notify.send_validation_failure_notification(
        submitter_email, list(catalogs) or [failing()]
    )


def feedback(**kwargs):
    args = {
        "submitter_email": "you@example.org",
        "issue_url": "https://github.com/portolan-sdi/portolan-registry/issues/9",
        "issue_title": "Every collection claims proprietary",
        "kind": "Data quality",
        "title": "Example Catalog",
    }
    return notify.send_feedback_notification("example", **{**args, **kwargs})


def removal(**kwargs):
    args = {
        "submitter_email": "you@example.org",
        "issue_url": "https://github.com/portolan-sdi/portolan-registry/issues/12",
        "issue_title": "Remove example: the publisher registers its own",
        "requester": "someone-else",
        "relationship": "I work for the publisher, and someone else registered it",
        "reason": "The publisher now registers its own catalog.",
        "title": "Example Catalog",
    }
    return notify.send_removal_request_notification("example", **{**args, **kwargs})


class TestStaleNotification:
    def test_sends_to_the_submitter(self, post):
        assert stale() is True
        payload = post.calls[0]["json"]
        assert payload["to"] == ["you@example.org"]
        assert payload["from"] == notify.FROM_ADDRESS
        assert "validation failed" in payload["subject"]
        assert "Example Catalog" in payload["subject"]

    def test_body_carries_the_reason_and_url(self, post):
        stale()
        html = post.calls[0]["json"]["html"]
        assert "404 Not Found" in html
        assert 'href="https://ex.org/catalog.json"' in html

    def test_subject_falls_back_to_the_id(self, post):
        stale(title=None)
        assert "example" in post.calls[0]["json"]["subject"]


class TestFeedbackNotification:
    def test_sends_to_the_submitter(self, post):
        assert feedback() is True
        payload = post.calls[0]["json"]
        assert payload["to"] == ["you@example.org"]
        assert "Feedback on your catalog" in payload["subject"]

    def test_body_links_the_issue_and_names_the_kind(self, post):
        feedback()
        html = post.calls[0]["json"]["html"]
        assert 'href="https://github.com/portolan-sdi/portolan-registry/issues/9"' in html
        assert "Every collection claims proprietary" in html
        assert "Data quality" in html

    def test_an_issue_title_cannot_inject_markup(self, post):
        feedback(issue_title="<script>alert(1)</script>")
        html = post.calls[0]["json"]["html"]
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestRemovalRequestNotification:
    def test_sends_to_the_submitter(self, post):
        assert removal() is True
        payload = post.calls[0]["json"]
        assert payload["to"] == ["you@example.org"]
        assert "Request to remove your catalog" in payload["subject"]
        assert "Example Catalog" in payload["subject"]

    def test_body_names_the_requester_their_reason_and_the_issue(self, post):
        removal()
        html = post.calls[0]["json"]["html"]
        assert "someone-else" in html
        assert "I work for the publisher, and someone else registered it" in html
        assert "The publisher now registers its own catalog." in html
        assert 'href="https://github.com/portolan-sdi/portolan-registry/issues/12"' in html

    def test_body_states_the_objection_window(self, post):
        removal()
        assert f"within {notify.OBJECTION_DAYS} days" in post.calls[0]["json"]["html"]

    @pytest.mark.parametrize("field", ["reason", "relationship", "requester", "issue_title"])
    def test_no_answer_can_inject_markup(self, post, field):
        removal(**{field: "<img src=x onerror=alert(1)>"})
        html = post.calls[0]["json"]["html"]
        assert "<img" not in html
        assert "&lt;img" in html

    def test_subject_falls_back_to_the_id(self, post):
        removal(title=None)
        assert "example" in post.calls[0]["json"]["subject"]


class TestDeclining:
    """Three reasons to send nothing, none of them an error."""

    def test_disabled(self, post):
        assert stale(enabled=False) is False
        assert feedback(enabled=False) is False
        assert removal(enabled=False) is False
        assert post.calls == []

    def test_no_api_key(self, post, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY")
        assert stale() is False
        assert feedback() is False
        assert post.calls == []

    def test_no_address_on_file(self, post):
        assert stale(submitter_email=None) is False
        assert feedback(submitter_email="") is False
        assert post.calls == []


class TestFailuresDoNotPropagate:
    def test_a_transport_error_returns_false(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        monkeypatch.setattr(notify.requests, "post", Recorder(raises=RuntimeError("no route")))
        assert stale() is False
        assert feedback() is False

    def test_a_rejected_send_returns_false(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        monkeypatch.setattr(notify.requests, "post", Recorder(status_code=422))
        assert feedback() is False


class TestDeprecatedStaleNotification:
    def test_names_the_successor(self, post):
        stale(deprecated=True, superseded_by="next-catalog", failure_reason="rashid PTL-1")
        html = post.calls[0]["json"]["html"]
        assert "This catalog is deprecated." in html
        assert "Its replacement is <code>next-catalog</code>" in html
        assert "rashid PTL-1" in html

    def test_without_a_successor_names_none(self, post):
        stale(deprecated=True)
        html = post.calls[0]["json"]["html"]
        assert "This catalog is deprecated." in html
        assert "replacement" not in html

    def test_a_catalog_that_is_not_deprecated_explains_unreachable_status(self, post):
        stale()
        html = post.calls[0]["json"]["html"]
        assert "deprecated" not in html
        assert "remains unreachable for 30 days" in html

    def test_the_successor_cannot_inject_markup(self, post):
        stale(deprecated=True, superseded_by="<b>x</b>")
        assert "<b>x</b>" not in post.calls[0]["json"]["html"]


class TestDeprecationRequest:
    def test_says_deprecate_in_the_subject_and_the_body(self, post):
        removal(action="deprecate")
        payload = post.calls[0]["json"]
        assert "Request to deprecate your catalog" in payload["subject"]
        assert "asked to deprecate" in payload["html"]
        assert "object to the deprecation" in payload["html"]

    def test_an_unknown_action_reads_as_a_removal(self, post):
        removal(action="explode")
        assert "Request to remove your catalog" in post.calls[0]["json"]["subject"]


class TestValidationFailureNotification:
    """The email for a catalog that starts to fail the validators (#252)."""

    def test_lists_each_failing_rule(self, post):
        assert validation_failure() is True
        mail = post.calls[0]["json"]
        assert mail["to"] == ["you@example.org"]
        assert mail["subject"] == "[Portolan Registry] Catalog validation failed: Example Catalog"
        assert "<code>PTL-PRV-002</code> (22 findings): a mirror needs a via link" in mail["html"]
        assert "<li>and 21 more</li>" in mail["html"]
        assert "its status stays <code>valid</code>" in mail["html"]
        assert "<strong>Failing since:</strong> 2026-10-19" in mail["html"]
        assert "each Monday while a catalog fails" in mail["html"]

    def test_one_email_covers_several_catalogs(self, post):
        validation_failure(failing("a", "A"), failing("b", "B"))
        mail = post.calls[0]["json"]
        assert len(post.calls) == 1
        assert mail["subject"] == "[Portolan Registry] Validation failed for 2 catalogs"
        assert "<h3>A</h3>" in mail["html"]
        assert "<h3>B</h3>" in mail["html"]

    def test_escapes_catalog_text(self, post):
        group = RuleGroup("rashid", "R", "d", 1, ["<img src=x>"])
        catalog = notify.FailingCatalog("x", "https://ex.org/c.json", [group], "2026", "<b>T</b>")
        validation_failure(catalog)
        body = post.calls[0]["json"]["html"]
        assert "<img" not in body
        assert "<b>T</b>" not in body
        assert "&lt;img src=x&gt;" in body

    def test_no_address_sends_nothing(self, post):
        assert validation_failure(submitter_email=None) is False
        assert post.calls == []
