"""Who made a catalog's data, who serves it, and whether it is a mirror.

portolan-spec states the rule and refuses to give it a field. From
specs/portolan/core.md, Source Provenance:

    Which kind a catalog is, is derived from its providers, not declared
    through any dedicated property. A catalog is official when its producer
    and host are the same organization; it is a mirror when they differ.

So the registry derives it. The same `providers` arrays name the parties, and
the export publishes both the derivation and its inputs, because a consumer
that disagrees with the reading needs the evidence to re-read it.

rashid is the canonical reading of the spec, and the registry gates on it.
`collection_kind` therefore applies rashid's rule (rashid.rules.provenance,
`provenance_of`) unchanged. A second reading here would let the export call a
catalog official while the validator in the same run reports it as a mirror.

The spec makes the collection-level declaration authoritative and asks
nothing of a Catalog, so the answer is assembled per collection and then
aggregated. See `catalog_kind` for what a catalog holding both kinds reports.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

PRODUCER = "producer"
PROCESSOR = "processor"
HOST = "host"


def _name(provider: dict[str, Any]) -> str | None:
    """The provider's name, trimmed, or None when it has no usable one."""
    name = provider.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def _roles_of(provider: dict[str, Any]) -> list[str]:
    roles = provider.get("roles")
    if not isinstance(roles, list):
        return []
    return [r for r in roles if isinstance(r, str)]


def _entries(providers: object) -> list[dict[str, Any]]:
    """The well-formed provider entries in a `providers` value."""
    if not isinstance(providers, list):
        return []
    return [p for p in providers if isinstance(p, dict)]


def collection_kind(providers: object) -> str | None:
    """ "official", "mirror", or None when the providers do not say.

    Official when a producer carries the host's name, compared without case,
    and mirror otherwise. This is rashid's rule. The URL plays no part, so
    planet-disasterdata reads as a mirror: it names the producer "Planet Labs
    PBC" and the host "Planet Crisis Response Program", and rashid reports
    that collection as a mirror with no `via` link.

    None when the providers break the rules that rashid checks first: no
    producer, a host count other than one, or a host with no name. Guessing a
    kind from providers that fail validation would put a claim on evidence
    the registry's own validator rejects.
    """
    entries = _entries(providers)
    producers = [p for p in entries if PRODUCER in _roles_of(p)]
    hosts = [p for p in entries if HOST in _roles_of(p)]
    if not producers or len(hosts) != 1:
        return None
    host_name = _name(hosts[0])
    if host_name is None:
        return None
    host_name = host_name.casefold()
    for producer in producers:
        name = _name(producer)
        if name is not None and name.casefold() == host_name:
            return "official"
    return "mirror"


def catalog_kind(kinds: Iterable[str | None]) -> str | None:
    """One answer for a catalog, from the kind of each collection beneath it.

    A catalog with even one mirrored collection reports "mirror". It re-hosts
    data it did not produce, and "official" is a claim to be the canonical
    home of everything under it. Where the two readings differ, the weaker
    one is the safe one.

    This does lose detail. carto-do produces six collections and re-hosts
    three from Kontur and OpenCellID, and reporting "mirror" describes the
    second half only. Naming that third state is worth doing once a consumer
    renders it; today the site maps anything other than official or mirror to
    no badge at all.
    """
    seen = {kind for kind in kinds if kind}
    if not seen:
        return None
    return "official" if seen == {"official"} else "mirror"


def _party(provider: dict[str, Any], name: str) -> dict[str, str]:
    """A provider trimmed to what names it. Descriptions can run long."""
    party = {"name": name}
    url = provider.get("url")
    if isinstance(url, str) and url:
        party["url"] = url
    return party


def parties(
    provider_lists: Iterable[object],
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, str] | None]:
    """The producers, the processors, and the host of a catalog.

    Three roles, three answers. The producer made the data. The processor
    derived this copy from it, which on a mirror is usually the party that
    built the mirror. The host serves it.

    Takes the `providers` of every collection, in crawl order. Producers and
    processors keep that order and drop repeats, compared on the name without
    case. The host is the first one seen: the spec allows one host per
    collection, and a catalog served from two places is not a shape the
    export models.

    soft-commodity-infrastructure credits MAPA at gov.br/agricultura and ANP
    at gov.br/anp. Both stay on the list, because the URL plays no part in
    the comparison.
    """
    producers: list[dict[str, str]] = []
    processors: list[dict[str, str]] = []
    seen_producer: set[str] = set()
    seen_processor: set[str] = set()
    host: dict[str, str] | None = None

    for providers in provider_lists:
        for provider in _entries(providers):
            name = _name(provider)
            if name is None:
                continue
            key = name.casefold()
            roles = _roles_of(provider)
            if PRODUCER in roles and key not in seen_producer:
                seen_producer.add(key)
                producers.append(_party(provider, name))
            if PROCESSOR in roles and key not in seen_processor:
                seen_processor.add(key)
                processors.append(_party(provider, name))
            if HOST in roles and host is None:
                host = _party(provider, name)

    return producers, processors, host
