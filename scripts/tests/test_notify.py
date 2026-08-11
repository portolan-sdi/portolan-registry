"""Mail to submitters: what gets sent, and when nothing does.

Resend is replaced with a recorder. What belongs to us is the payload, the
three conditions under which we decline to send, and the promise that a
failure here never propagates: both senders are called from runs that must
finish regardless.
"""

from __future__ import annotations

import pytest

from registry import notify


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


def feedback(**kwargs):
    args = {
        "submitter_email": "you@example.org",
        "issue_url": "https://github.com/portolan-sdi/portolan-registry/issues/9",
        "issue_title": "Every collection claims proprietary",
        "kind": "Data quality",
        "title": "Example Catalog",
    }
    return notify.send_feedback_notification("example", **{**args, **kwargs})


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
        assert (
            'href="https://github.com/portolan-sdi/portolan-registry/issues/9"' in html
        )
        assert "Every collection claims proprietary" in html
        assert "Data quality" in html

    def test_an_issue_title_cannot_inject_markup(self, post):
        feedback(issue_title="<script>alert(1)</script>")
        html = post.calls[0]["json"]["html"]
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestDeclining:
    """Three reasons to send nothing, none of them an error."""

    def test_disabled(self, post):
        assert stale(enabled=False) is False
        assert feedback(enabled=False) is False
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
        monkeypatch.setattr(
            notify.requests, "post", Recorder(raises=RuntimeError("no route"))
        )
        assert stale() is False
        assert feedback() is False

    def test_a_rejected_send_returns_false(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        monkeypatch.setattr(notify.requests, "post", Recorder(status_code=422))
        assert feedback() is False
