"""Who made a catalog's data, who serves it, and whether it is a mirror.

portolan-spec states the rule and refuses to give it a field. From
specs/portolan/core.md, Source Provenance:

    Which kind a catalog is, is derived from its providers, not declared
    through any dedicated property. A catalog is official when its producer
    and host are the same organization; it is a mirror when they differ.

So the registry derives it. The same `providers` arrays name the parties, and
the export publishes both the derivation and its inputs, because a consumer
that disagrees with the reading needs the evidence to re-read it.

The spec asks every Collection for `providers` and asks nothing of a Catalog,
so the answer is assembled per collection and then aggregated. See
`catalog_kind` for what a catalog holding both kinds reports.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from urllib.parse import urlsplit

PRODUCER = "producer"
HOST = "host"


def _netloc(url: object) -> str:
    """Host of a URL, lowercased, without `www.` or a port."""
    if not isinstance(url, str) or not url:
        return ""
    host = urlsplit(url).netloc.casefold().partition(":")[0]
    return host.removeprefix("www.")


def _identities(provider: Mapping) -> set[str]:
    """Every handle by which this provider entry names one organization.

    Two entries are the same organization when these sets intersect, so a URL
    can vouch for a name and a name for a URL. Comparing on names alone reads
    one organization writing itself two ways as two parties:
    planet-disasterdata names the producer "Planet Labs PBC" and the host
    "Planet Crisis Response Program", both at planet.com. Comparing on URLs
    alone fails the other way, because the spec lets a producer declare a name
    and no URL at all.

    Hosts compare whole rather than by registrable domain, so a shared public
    suffix is not by itself a shared organization: `co.uk` matches nothing.

    One organization per host is still an assumption, and a government that
    puts every agency on one host defeats it. A producer at `gov.br/florestal`
    and a host at `gov.br/incra` are two agencies, and this reads them as one.
    No registered catalog is in that position: for all 19, the kind derived
    here agrees with the `via` link the specification requires of a mirror.
    Splitting on the path would need a rule for how much of it names the
    organization, which the data does not support today.
    """
    handles = set()
    name = (provider.get("name") or "").strip().casefold()
    if name:
        handles.add(f"name:{name}")
    host = _netloc(provider.get("url"))
    if host:
        handles.add(f"host:{host}")
    return handles


def _roles_of(provider: Mapping) -> list:
    roles = provider.get("roles")
    return roles if isinstance(roles, list) else []


def _entries(providers: object) -> list[Mapping]:
    """The well-formed provider entries in a `providers` value."""
    if not isinstance(providers, Sequence) or isinstance(providers, str):
        return []
    return [p for p in providers if isinstance(p, Mapping)]


def collection_kind(providers: object) -> str | None:
    """"official", "mirror", or None when the providers do not say.

    None is the honest answer for a collection that names no producer or no
    host. Two registered catalogs are in that position today, and guessing
    "official" for them would put the stronger claim on the weaker evidence.

    The spec allows exactly one host, so the loop over hosts is tolerance for
    a catalog that breaks that rule rather than a case the spec defines. Every
    host must produce for the collection to read as official.
    """
    producers: set[str] = set()
    hosts: list[set[str]] = []
    for provider in _entries(providers):
        roles = _roles_of(provider)
        handles = _identities(provider)
        if PRODUCER in roles:
            producers |= handles
        if HOST in roles:
            hosts.append(handles)

    if not producers or not hosts:
        return None
    return "official" if all(host & producers for host in hosts) else "mirror"


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


def _party(provider: Mapping) -> dict:
    """A provider trimmed to what names it. Descriptions can run long."""
    party = {"name": (provider.get("name") or "").strip()}
    url = provider.get("url")
    if isinstance(url, str) and url:
        party["url"] = url
    return party


def parties(provider_lists: Iterable[object]) -> tuple[list[dict], dict | None]:
    """The producers behind a catalog, and the host that serves it.

    Takes the `providers` of the registered root first, when it declares any,
    then of every collection beneath. Producers keep that order and drop
    repeats, so the first one named is the root's own first choice where the
    root speaks, and otherwise the first collection's. The host is the first
    one seen, on the same reasoning: the spec allows one host per collection,
    and a catalog served from two places is not a shape the export models.

    Repeats are judged on the name alone, not on the looser identity
    `collection_kind` compares. Sharing a host does not make two organizations
    one: soft-commodity-infrastructure credits MAPA at gov.br/agricultura and
    ANP at gov.br/anp, and matching on the host drops ANP off the list of who
    made the data. Crediting one organization twice under two names is the
    lesser fault, and the catalog chose to write both.
    """
    producers: list[dict] = []
    seen: set[str] = set()
    host: dict | None = None

    for providers in provider_lists:
        for provider in _entries(providers):
            roles = _roles_of(provider)
            name = (provider.get("name") or "").strip().casefold()
            if not name:
                continue
            if PRODUCER in roles and name not in seen:
                seen.add(name)
                producers.append(_party(provider))
            if HOST in roles and host is None:
                host = _party(provider)

    return producers, host
