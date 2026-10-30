"""Classify the difference between two versions of a contract schema.

The rules come from schema/README.md. A consumer validates with one
released schema and must keep working on data from every later minor and
patch release. So a change that lets the registry write data the old schema
rejects, or that drops a field a consumer reads, is breaking.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from registry.contract import self_pattern

PATCH, MINOR, MAJOR = 1, 2, 3
# The kind of each change. The version step that a kind needs depends on the
# release line: see `step` and `minimum_version`.
LEVEL_NAMES = {PATCH: "documentation", MINOR: "additive", MAJOR: "breaking"}

# Keywords that only document. A change to them is a patch.
ANNOTATIONS = frozenset({"description", "title", "$comment", "examples"})

# Keywords whose keys name fields or definitions.
_NAMED = frozenset({"properties", "definitions"})

# Keywords whose value is a set. `required` has its own rule in `_walk`.
_UNORDERED = frozenset({"enum", "type"})

SELF = "<self>"


def _as_set(value: Any) -> set[str]:
    """The items of an `enum` or `type` value. A bare `type` string is a set of one."""
    items = value if isinstance(value, list) else [value]
    return {repr(item) for item in items}


@dataclass(frozen=True)
class Change:
    level: int
    path: str
    detail: str

    def __str__(self) -> str:
        return f"{LEVEL_NAMES[self.level]}: {self.path or '(root)'}: {self.detail}"


def _normalize(node: Any, own_id: str) -> Any:
    """Replace the schema's own URL and its pattern, which change with each version."""
    if isinstance(node, str):
        return SELF if node in (own_id, own_id.rstrip("#"), self_pattern(own_id)) else node
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
            elif key in _UNORDERED:
                # The order of these lists has no meaning, so compare them as sets.
                if _as_set(old[key]) != _as_set(new[key]):
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
    """The kind of change that the step from `old` to `new` allows.

    None when `new` is not later. Before 1.0.0, a new minor version starts a
    new release line, so it allows a MAJOR change, and a new patch version
    allows a MINOR change. This is the rule that Cargo applies to 0.x.
    """
    if new <= old:
        return None
    if new[0] > old[0] or (old[0] == 0 and new[1] > old[1]):
        return MAJOR
    if old[0] == 0 or new[1] > old[1]:
        return MINOR
    return PATCH


def minimum_version(old: tuple[int, int, int], level: int) -> tuple[int, int, int]:
    """The first version after `old` that allows a change of `level`."""
    major, minor, patch = old
    if level == MAJOR:
        return (0, minor + 1, 0) if major == 0 else (major + 1, 0, 0)
    if level == MINOR and major > 0:
        return major, minor + 1, 0
    return major, minor, patch + 1
