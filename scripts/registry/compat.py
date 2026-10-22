"""Classify the difference between two versions of a contract schema.

The rules come from schema/CHANGELOG.md. A consumer validates with one
released schema and must keep working on data from every later minor and
patch release. So a change that lets the registry write data the old schema
rejects, or that drops a field a consumer reads, is major.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PATCH, MINOR, MAJOR = 1, 2, 3
LEVEL_NAMES = {PATCH: "patch", MINOR: "minor", MAJOR: "major"}

# Keywords that only document. A change to them is a patch.
ANNOTATIONS = frozenset({"description", "title", "$comment", "examples"})

# Keywords whose keys name fields or definitions.
_NAMED = frozenset({"properties", "definitions"})

SELF = "<self>"


@dataclass(frozen=True)
class Change:
    level: int
    path: str
    detail: str

    def __str__(self) -> str:
        return f"{LEVEL_NAMES[self.level]}: {self.path or '(root)'}: {self.detail}"


def _normalize(node: Any, own_id: str) -> Any:
    """Replace the schema's own URL, which changes with each version."""
    if isinstance(node, str):
        return SELF if node in (own_id, own_id.rstrip("#")) else node
    if isinstance(node, dict):
        return {k: _normalize(v, own_id) for k, v in node.items() if k != "$id"}
    if isinstance(node, list):
        return [_normalize(v, own_id) for v in node]
    return node


def _named(old: Mapping[str, Any], new: Mapping[str, Any], path: str) -> list[Change]:
    changes = []
    for name in sorted(old.keys() - new.keys()):
        changes.append(Change(MAJOR, f"{path}/{name}", "removed"))
    for name in sorted(new.keys() - old.keys()):
        changes.append(Change(MINOR, f"{path}/{name}", "added"))
    for name in sorted(old.keys() & new.keys()):
        changes.extend(_walk(old[name], new[name], f"{path}/{name}"))
    return changes


def _walk(old: Any, new: Any, path: str) -> list[Change]:
    if isinstance(old, dict) and isinstance(new, dict):
        changes = []
        for key in sorted(old.keys() | new.keys()):
            here = f"{path}/{key}"
            if key in ANNOTATIONS:
                if old.get(key) != new.get(key):
                    changes.append(Change(PATCH, here, "documentation changed"))
            elif key not in old or key not in new:
                verb = "added" if key in new else "removed"
                changes.append(Change(MAJOR, here, f"keyword {verb}"))
            elif key in _NAMED:
                changes.extend(_named(old[key], new[key], here))
            elif key == "required":
                for name in sorted(set(old[key]) - set(new[key])):
                    changes.append(Change(MAJOR, f"{path}/properties/{name}", "no longer required"))
                for name in sorted(set(new[key]) - set(old[key])):
                    changes.append(Change(MINOR, f"{path}/properties/{name}", "now required"))
            elif key == "enum":
                if set(map(repr, old[key])) != set(map(repr, new[key])):
                    changes.append(Change(MAJOR, here, f"{old[key]} -> {new[key]}"))
            else:
                changes.extend(_walk(old[key], new[key], here))
        return changes
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [
            c
            for i, (a, b) in enumerate(zip(old, new, strict=True))
            for c in _walk(a, b, f"{path}/{i}")
        ]
    if old != new:
        return [Change(MAJOR, path, f"{old!r} -> {new!r}")]
    return []


def classify(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[Change]:
    """Every change from `old` to `new`, each with the version step it needs."""
    return _walk(_normalize(old, old.get("$id", "")), _normalize(new, new.get("$id", "")), "")


def required_level(changes: list[Change]) -> int | None:
    return max((c.level for c in changes), default=None)


def step(old: tuple[int, int, int], new: tuple[int, int, int]) -> int | None:
    """The version step from `old` to `new`. None when `new` is not later."""
    if new <= old:
        return None
    if new[0] > old[0]:
        return MAJOR
    if new[1] > old[1]:
        return MINOR
    return PATCH
