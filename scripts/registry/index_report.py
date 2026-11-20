"""Report the result of one catalog index build.

The Catalog Index workflow shows the report on the run page. The job summary
lists each catalog with a problem. A warning annotation names each one, so a
run that ends with success still shows the catalogs that it did not read.

The text of a problem can come from another host, such as an error message
with a URL. The report escapes it, so it cannot start a workflow command or
break the summary table.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from registry.catalog_index import Walk

# The most characters of one problem in the report. An error message can
# hold a part of a response body.
MAX_DETAIL = 300

READ = "Read"
HOLES = "Read with holes"
FAILED = "Failed"
SKIPPED = "Skipped"


@dataclass
class Outcome:
    """The result of one registered catalog in one build.

    `problem` says why the build did not read the catalog. `kept` is the
    number of rows that the catalog keeps from the previous index.
    """

    catalog_id: str
    walk: Walk | None = None
    problem: str | None = None
    skipped: bool = False
    kept: int = 0

    @property
    def result(self) -> str:
        if self.skipped:
            return SKIPPED
        if self.walk is None:
            return FAILED
        return READ if self.walk.complete else HOLES

    @property
    def detail(self) -> str:
        """The problem in one line, or an empty string for a catalog with none."""
        if self.problem is not None:
            text = self.problem
        elif self.walk is not None and self.walk.holes:
            count = len(self.walk.holes)
            text = f"{count} document(s) not read, such as {self.walk.holes[0]}"
        else:
            return ""
        text = " ".join(text.split())
        return text if len(text) <= MAX_DETAIL else text[: MAX_DETAIL - 3] + "..."


def _command_data(text: str) -> str:
    """Escape the data of a workflow command, as the GitHub Actions toolkit does."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def warning_commands(outcomes: Sequence[Outcome]) -> list[str]:
    """One warning command for each catalog with a problem."""
    lines = []
    for outcome in outcomes:
        if outcome.result == READ:
            continue
        message = f"{outcome.catalog_id}: {outcome.result.lower()}. {outcome.detail}"
        if outcome.kept:
            message += f" It keeps {outcome.kept} row(s) from the previous index."
        lines.append(f"::warning title=Catalog index::{_command_data(message)}")
    return lines


def _cell(text: str) -> str:
    """Text that a Markdown table cell shows as written."""
    for char in "\\|`*_[]<>":
        text = text.replace(char, "\\" + char)
    return text


def summary(outcomes: Sequence[Outcome], headline: str) -> str:
    """The job summary in Markdown."""
    lines = ["## Catalog index", "", headline, "", "| Result | Catalogs |", "| --- | ---: |"]
    for result in (READ, HOLES, FAILED, SKIPPED):
        count = sum(1 for outcome in outcomes if outcome.result == result)
        lines.append(f"| {result} | {count} |")
    problems = [outcome for outcome in outcomes if outcome.result != READ]
    if problems:
        lines += [
            "",
            "### Catalogs with a problem",
            "",
            "| Catalog | Result | Rows kept from the previous index | Detail |",
            "| --- | --- | ---: | --- |",
        ]
        for outcome in problems:
            lines.append(
                f"| {_cell(outcome.catalog_id)} | {outcome.result} | {outcome.kept} "
                f"| {_cell(outcome.detail)} |"
            )
    return "\n".join(lines) + "\n"
