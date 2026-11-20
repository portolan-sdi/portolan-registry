"""The report of one catalog index build: the job summary and the warnings."""

from __future__ import annotations

from registry.catalog_index import Walk
from registry.index_report import (
    FAILED,
    HOLES,
    MAX_DETAIL,
    READ,
    SKIPPED,
    Outcome,
    summary,
    warning_commands,
)

HOLE = "https://ex.org/sub/catalog.json"


def outcomes():
    return [
        Outcome("good", walk=Walk(rows=[])),
        Outcome("holey", walk=Walk(rows=[], holes=[HOLE, "https://ex.org/b.json"]), kept=3),
        Outcome("down", problem="https://ex.org/catalog.json returns text/html, not JSON", kept=7),
        Outcome("empty", problem="The entry has no URL.", skipped=True),
    ]


def test_each_outcome_has_one_result():
    assert [outcome.result for outcome in outcomes()] == [READ, HOLES, FAILED, SKIPPED]


def test_the_detail_of_a_walk_with_holes_names_one_hole():
    assert outcomes()[1].detail == f"2 document(s) not read, such as {HOLE}"
    assert outcomes()[0].detail == ""


def test_the_detail_is_one_short_line():
    detail = Outcome("x", problem="a\nb\r\n" + "c" * 1000).detail
    assert "\n" not in detail and "\r" not in detail
    assert len(detail) == MAX_DETAIL and detail.endswith("...")


def test_one_warning_for_each_catalog_with_a_problem():
    lines = warning_commands(outcomes())
    assert len(lines) == 3
    assert all(line.startswith("::warning title=Catalog index::") for line in lines)
    assert lines[1] == (
        "::warning title=Catalog index::down: failed. https://ex.org/catalog.json returns "
        "text/html, not JSON It keeps 7 row(s) from the previous index."
    )


def test_a_warning_cannot_start_a_second_workflow_command():
    """The text of a problem can come from another host."""
    (line,) = warning_commands([Outcome("x", problem="100% bad")])
    assert line.endswith("100%25 bad")
    assert "\n" not in line


def test_the_summary_counts_each_result_and_lists_each_problem():
    text = summary(outcomes(), "The build wrote 10 rows from 4 catalogs.")
    assert text.startswith("## Catalog index\n\nThe build wrote 10 rows from 4 catalogs.\n")
    for result in (READ, HOLES, FAILED, SKIPPED):
        assert f"| {result} | 1 |" in text
    assert "| down | Failed | 7 | https://ex.org/catalog.json returns text/html, not JSON |" in text
    assert "| good |" not in text


def test_the_summary_of_a_run_with_no_problem_has_no_problem_table():
    text = summary([Outcome("good", walk=Walk(rows=[]))], "No row changed.")
    assert "Catalogs with a problem" not in text


def test_a_problem_cannot_break_the_summary_table():
    text = summary([Outcome("x", problem="a | b <img> [link](u)")], "h")
    assert r"a \| b \<img\> \[link\](u)" in text
