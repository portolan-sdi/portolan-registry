"""Who made a catalog's data, who serves it, and whether it is a mirror.

portolan-spec core.md, Providers: every collection names at least one
`producer` and exactly one `host`, listed last. The collection-level
declaration is authoritative, so the registry reads every collection and
ignores the root's `providers`.

portolan-spec core.md, Source Provenance: a catalog is official when its
producer and host are the same organization, and a mirror when they differ.
No property declares the kind. The registry derives it as follows:

- A collection is official when its host is one of its producers. It is a
  mirror when it names a producer and a host and the host is none of them.
  It decides nothing when it names no producer or no host.
- A catalog is a mirror when any collection is a mirror. An organization that
  hosts data it did not produce is a mirror under the spec, even when it
  also publishes data of its own. A catalog is official when at least one
  collection is official and none is a mirror. Otherwise its kind is null.

The spec does not say how to tell that two providers are the same
organization. The registry compares names with case and runs of whitespace
ignored. URLs do not decide it: a producer `url` often points at a dataset
page, and a host `url` at a contact page.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from registry.crawl import CollectionSummary

Kind = Literal["official", "mirror"]


class Party(TypedDict, total=False):
    """One organization, as `{name, url}`. `url` is absent when not given.

    The provider `description` is dropped. It runs to two sentences in some
    catalogs, and the export names parties, it does not describe them.
    """

    name: str
    url: str


class Provenance(TypedDict):
    kind: Kind | None
    producers: list[Party]
    processors: list[Party]
    host: Party | None


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _party(provider: object) -> Party | None:
    if not isinstance(provider, dict):
        return None
    name = provider.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    party: Party = {"name": name.strip()}
    url = provider.get("url")
    if isinstance(url, str) and url.strip():
        party["url"] = url.strip()
    return party


def collection_parties(
    providers: object,
) -> tuple[list[Party], list[Party], Party | None]:
    """Producers, processors, and the host one collection's providers name."""
    producers: list[Party] = []
    processors: list[Party] = []
    host: Party | None = None
    if not isinstance(providers, list):
        return producers, processors, host
    for provider in providers:
        party = _party(provider)
        if party is None:
            continue
        roles = provider.get("roles") or []
        if not isinstance(roles, list):
            continue
        if "producer" in roles:
            producers.append(party)
        if "processor" in roles:
            processors.append(party)
        # The spec lists the one host last. A collection that names two
        # breaks that rule, and the last one is still where the spec says to
        # look.
        if "host" in roles:
            host = party
    return producers, processors, host


def _collection_kind(producers: Sequence[Party], host: Party | None) -> Kind | None:
    if not producers or host is None:
        return None
    produced_by = {_key(p["name"]) for p in producers}
    return "official" if _key(host["name"]) in produced_by else "mirror"


def _distinct(parties: Iterable[Party]) -> list[Party]:
    """One entry per organization, in the order first named.

    The first URL named stands. A later one only fills a party that had none.
    """
    by_key: dict[str, Party] = {}
    for party in parties:
        key = _key(party["name"])
        if key not in by_key:
            by_key[key] = Party(**party)
        elif "url" not in by_key[key] and "url" in party:
            by_key[key]["url"] = party["url"]
    return list(by_key.values())


def catalog_provenance(collections: Sequence[CollectionSummary]) -> Provenance:
    """Aggregate the parties and the kind over every collection of a catalog."""
    kinds = {_collection_kind(c.producers, c.host) for c in collections}
    kind: Kind | None
    if "mirror" in kinds:
        kind = "mirror"
    elif "official" in kinds:
        kind = "official"
    else:
        kind = None

    # The host most collections name. A tie goes to the host named first,
    # because Counter.most_common keeps insertion order among equal counts.
    hosts = [c.host for c in collections if c.host is not None]
    host: Party | None = None
    if hosts:
        winner, _ = Counter(_key(h["name"]) for h in hosts).most_common(1)[0]
        host = next(p for p in _distinct(hosts) if _key(p["name"]) == winner)

    return {
        "kind": kind,
        "producers": _distinct(p for c in collections for p in c.producers),
        "processors": _distinct(p for c in collections for p in c.processors),
        "host": host,
    }
