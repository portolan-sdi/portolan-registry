"""Build the catalog index of the registry as one GeoParquet file.

A catalog index has one row for each catalog and each collection of a catalog
tree. portolan-spec defines the layout in specs/incubating/stac-geoparquet.md.
The layout is experimental and follows radiantearth/stac-geoparquet-spec#17.

The registry file is a federated index. It holds the tree of each registered
catalog. The row of a registered catalog has an empty `parents` list. Each
`href` is absolute, because the trees have different hosts.

The file adds one column to the layout, `portolan_registry:id`. It holds the
registry ID of the registered catalog that the row belongs to, so a client
can join a row to `catalogs.parquet`. A collection `id` is unique only in its
own catalog.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely

from registry.bbox import collection_bbox
from registry.crawl import temporal_interval
from registry.export import PREFIX
from registry.fetch import Fetcher, NotFound, resolve_url
from registry.geoparquet import GEOMETRY, GEOPARQUET_VERSION, bbox_geometry
from registry.report import log
from registry.status import parse_timestamp

# The build writes the file here, and the site build reads it from here. Git
# ignores the path. The published copy lives on INDEX_BRANCH.
INDEX_PATH = Path("exports/collections.parquet")

# The file name on the site and on INDEX_BRANCH.
INDEX_NAME = "collections.parquet"

# One commit, replaced by each update. The file is about 9 MB and most of
# its bytes change with each update, so a history on main would add that
# size to every clone for each night with a change.
INDEX_BRANCH = "bot/catalog-index"

MEDIA_TYPE = "application/vnd.apache.parquet"

REGISTRY_ID = PREFIX + "id"

CONTAINER_TYPES = ("Catalog", "Collection")

# The most documents one walk reads. comunidad-madrid-datos-abiertos, the
# largest registered tree, had 2,141 catalogs and collections in November
# 2026. A server that makes up a new child URL on each request would
# otherwise give a walk with no end.
MAX_DOCUMENTS = 50_000

# The most text one walk keeps, as the sum of the lengths of `original`.
# ftw-global-data-2e, the largest registered tree by this measure, had 15
# million characters in November 2026. One document can have 32 MB
# (registry.fetch), so the document limit alone does not bound the memory.
# The walk can pass this limit by one batch of WALK_BATCH documents.
MAX_WALK_CHARS = 128 * 1024 * 1024

# The documents that one walk fetches before it counts their characters. The
# walk holds at most this many documents that it did not count.
WALK_BATCH = 64

# The threads that read one catalog. source.coop took more than 1 second for
# each document in November 2026, and ftw-global-data-2e has 507 of them.
WALK_WORKERS = 16

_PARENT = pa.struct([("id", pa.string()), ("href", pa.string())])
_LINK = pa.struct(
    [("href", pa.string()), ("rel", pa.string()), ("type", pa.string()), ("title", pa.string())]
)
_BBOX = pa.struct(
    [("xmin", pa.float64()), ("ymin", pa.float64()), ("xmax", pa.float64()), ("ymax", pa.float64())]
)

# The columns in the order of the specification table, then the registry column.
SCHEMA = pa.schema(
    [
        ("type", pa.string()),
        ("id", pa.string()),
        ("href", pa.string()),
        ("links", pa.list_(_LINK)),
        ("parents", pa.list_(_PARENT)),
        (GEOMETRY, pa.binary()),
        ("bbox", _BBOX),
        ("start_datetime", pa.timestamp("us", tz="UTC")),
        ("end_datetime", pa.timestamp("us", tz="UTC")),
        ("description", pa.string()),
        ("license", pa.string()),
        # The Parquet JSON type. The indexes that catalogs publish use it too.
        ("original", pa.json_(pa.string())),
        ("title", pa.string()),
        ("keywords", pa.list_(pa.string())),
        (REGISTRY_ID, pa.string()),
    ]
)

Row = dict[str, Any]


@dataclass
class Walk:
    """The rows of one registered catalog.

    `complete` is False when a document did not fetch for a reason other
    than a missing file, or when the walk stopped at a limit. The rows then
    have a hole that says nothing about the catalog. `holes` holds the URL
    of each document that the walk did not read.
    """

    rows: list[Row]
    holes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.holes


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _container_id(doc: Any) -> str | None:
    """The `id` of a catalog or collection document, or None for any other value."""
    if not isinstance(doc, dict) or doc.get("type") not in CONTAINER_TYPES:
        return None
    return _text(doc.get("id")) or None


def _links(doc: Mapping[str, Any]) -> list[dict[str, str | None]]:
    """The links of a document, as the document writes them."""
    links = doc.get("links")
    return [
        {name: _text(link.get(name)) for name in ("href", "rel", "type", "title")}
        for link in (links if isinstance(links, list) else [])
        if isinstance(link, dict) and _text(link.get("href"))
    ]


def _child_urls(url: str, doc: Mapping[str, Any]) -> list[str]:
    return [resolve_url(url, link["href"] or "") for link in _links(doc) if link["rel"] == "child"]


def _instant(value: str | None) -> datetime | None:
    """One end of a temporal interval in UTC. None for an open end."""
    parsed = parse_timestamp(value)
    return None if parsed is None else parsed.astimezone(UTC)


def _interval(collection: Mapping[str, Any]) -> tuple[datetime | None, datetime | None]:
    """The first temporal interval of a collection. STAC makes it the overall extent.

    The rules are those of the crawl (registry.crawl.temporal_interval), so
    this file and `catalogs.parquet` agree. A null end is an open end. The
    two ends are None when one end is not an RFC 3339 date-time, because a
    client reads a null end as an open end.
    """
    try:
        declared = temporal_interval(collection)
        if declared is None:
            return None, None
        return _instant(declared[0]), _instant(declared[1])
    except (AttributeError, LookupError, OverflowError, TypeError, ValueError):
        # The extent does not have the shape that STAC requires, or UTC
        # cannot hold the instant.
        return None, None


def _extent_columns(collection: Mapping[str, Any]) -> Row:
    """The geometry, bbox, and time columns of a collection row."""
    try:
        bbox = collection_bbox(collection)
    except (AttributeError, LookupError, TypeError, ValueError):
        # The extent does not have the shape that STAC requires.
        bbox = None
    geometry = bbox_geometry(bbox)
    start, end = _interval(collection)
    columns: Row = {GEOMETRY: None, "bbox": None, "start_datetime": start, "end_datetime": end}
    if bbox is not None and geometry is not None:
        half = len(bbox) // 2
        columns[GEOMETRY] = shapely.to_wkb(geometry, flavor="iso")
        # The values of the document. `xmin` is greater than `xmax` when the
        # bbox crosses the antimeridian, as the GeoParquet covering allows.
        columns["bbox"] = {
            "xmin": float(bbox[0]),
            "ymin": float(bbox[1]),
            "xmax": float(bbox[half]),
            "ymax": float(bbox[half + 1]),
        }
    return columns


def _finite(value: Any) -> Any:
    """`value` with null in the place of each NaN and each infinite number."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite(item) for item in value]
    return value


def _original(doc: Mapping[str, Any]) -> str:
    """The document as JSON text.

    Python reads `NaN` and `Infinity` from a document, and JSON has neither.
    A client that parses the column would fail on such a row, so each one
    becomes null.
    """
    try:
        return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except ValueError:
        return json.dumps(_finite(doc), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def build_row(catalog_id: str, url: str, doc: Mapping[str, Any], parents: Sequence[Row]) -> Row:
    """The row of one catalog or collection document."""
    is_collection = doc["type"] == "Collection"
    keywords = doc.get("keywords")
    row: Row = {
        "type": doc["type"],
        "id": doc["id"],
        "href": url,
        "links": _links(doc),
        "parents": [dict(parent) for parent in parents],
        GEOMETRY: None,
        "bbox": None,
        "start_datetime": None,
        "end_datetime": None,
        "description": _text(doc.get("description")),
        "license": None,
        "original": _original(doc),
        "title": _text(doc.get("title")),
        "keywords": None,
        REGISTRY_ID: catalog_id,
    }
    if is_collection:
        row.update(_extent_columns(doc))
        row["license"] = _text(doc.get("license"))
        if isinstance(keywords, list):
            row["keywords"] = [word for word in keywords if isinstance(word, str)]
    return row


def _fetch_children(
    targets: Sequence[tuple[str, list[Row]]],
    fetch: Callable[[str], Any],
    pool: ThreadPoolExecutor,
    walk: Walk,
) -> list[tuple[str, dict[str, Any], list[Row]]]:
    """Fetch one wave of children. Return each catalog and collection among them."""
    found = []
    children = pool.map(fetch, [url for url, _ in targets])
    for (url, parents), child in zip(targets, children, strict=True):
        if isinstance(child, NotFound):
            log(f"  Warning: missing child {url}")
        elif isinstance(child, Exception):
            log(f"  Warning: Failed to fetch {url}: {child}")
            walk.holes.append(url)
        elif _container_id(child) is None:
            log(f"  Warning: {url} is not a catalog or collection with an id")
        else:
            found.append((url, child, parents))
    return found


def _add_children(
    urls: Sequence[str],
    parents: list[Row],
    seen: set[str],
    targets: list[tuple[str, list[Row]]],
    max_documents: int,
) -> str | None:
    """Add each new child URL to `targets`. Return the limit that stops it, or None."""
    for url in urls:
        if url in seen:
            continue
        if len(seen) >= max_documents:
            return f"links more than {max_documents} documents"
        seen.add(url)
        targets.append((url, parents))
    return None


def walk_catalog(
    catalog_id: str,
    url: str,
    fetcher_factory: Callable[[], Fetcher],
    *,
    workers: int = WALK_WORKERS,
    max_documents: int = MAX_DOCUMENTS,
    max_chars: int = MAX_WALK_CHARS,
    deadline: float | None = None,
) -> Walk:
    """Read the tree below `url` and return one row for each catalog and collection.

    The walk follows `child` links from catalogs and from collections, because
    STAC lets a collection be a parent. It reads no item. It reads the tree
    level by level, in batches of WALK_BATCH documents.

    `fetcher_factory` builds one fetcher for each worker thread, because
    HttpFetcher makes no thread-safety promise for a shared session.

    `deadline` is a time.monotonic() value. After it, the walk starts no
    fetch, so a slow host cannot hold the build until the job times out.

    Raises when the root does not fetch, or when it is not a catalog or a
    collection with an `id`. A child that is missing is a fact about the
    catalog, so the walk skips it. A child that fails for another reason
    is a hole in the walk. So is each document that the walk does not read
    because of `max_documents`, `max_chars`, or `deadline`.
    """
    local = threading.local()

    def fetch(target: str) -> Any:
        if deadline is not None and time.monotonic() > deadline:
            return TimeoutError("the build has no time left")
        if not hasattr(local, "fetcher"):
            local.fetcher = fetcher_factory()
        try:
            return local.fetcher.get_json(target)
        except Exception as e:
            return e

    root = fetch(url)
    if isinstance(root, Exception):
        raise root
    if _container_id(root) is None:
        raise ValueError(f"{url} is not a STAC catalog or collection with an id")

    walk = Walk(rows=[])
    seen = {url}
    chars = 0
    limit: str | None = None
    queue: list[tuple[str, list[Row]]] = []
    start = 0
    found: list[tuple[str, dict[str, Any], list[Row]]] = [(url, root, [])]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            for doc_url, doc, parents in found:
                row = build_row(catalog_id, doc_url, doc, parents)
                chars += len(row["original"])
                if chars > max_chars:
                    limit = f"has more than {max_chars} characters of documents"
                    walk.holes.append(doc_url)
                    continue
                walk.rows.append(row)
                if limit is None:
                    below = [*parents, {"id": doc["id"], "href": doc_url}]
                    urls = _child_urls(doc_url, doc)
                    limit = _add_children(urls, below, seen, queue, max_documents)
                if limit is not None and _child_urls(doc_url, doc):
                    # The walk did not queue each child of this document.
                    walk.holes.append(doc_url)
            found = []
            # A batch can give no document, when each of its URLs fails.
            while not found and start < len(queue) and chars <= max_chars:
                batch = queue[start : start + WALK_BATCH]
                start += len(batch)
                found = _fetch_children(batch, fetch, pool, walk)
            if not found:
                break
    walk.holes.extend(target for target, _ in queue[start:])
    if limit is not None:
        log(f"  Warning: {catalog_id} {limit}; the walk stops")
    return walk


def check_rows(rows: Sequence[Row]) -> None:
    """Raise when Arrow cannot hold the rows of one catalog.

    A JSON document can hold text that is not valid Unicode, such as one
    half of a surrogate pair. Arrow refuses it. Without this check per
    catalog, one such document stops the index of every catalog.
    """
    pa.Table.from_pylist(list(rows), schema=SCHEMA)


def previous_rows(previous: pa.Table | None, catalog_id: str) -> list[Row]:
    """The rows that the previous index holds for one registered catalog."""
    if previous is None:
        return []
    mask = pc.equal(previous.column(REGISTRY_ID), catalog_id)
    rows: list[Row] = previous.filter(mask).to_pylist()
    return rows


def _below_hole(row: Row, holes: set[str]) -> bool:
    """True when `row` is a document that the walk did not read, or is below one."""
    return row["href"] in holes or any(parent["href"] in holes for parent in row["parents"])


def settle_rows(catalog_id: str, walk: Walk | None, previous: pa.Table | None) -> list[Row]:
    """The rows to publish for one catalog after this run.

    A catalog that did not answer keeps the rows of the previous index.
    Without this, one slow night removes the catalog from search and adds it
    again the next night.

    A walk with holes publishes the rows that it read. For each hole, it
    also keeps the previous rows of that document and of the documents below
    it. So a child that fails each night does not stop the other rows of
    the catalog from changing.
    """
    if walk is not None and walk.complete:
        return walk.rows
    carried = previous_rows(previous, catalog_id)
    if walk is not None:
        holes = set(walk.holes)
        read = {row["href"] for row in walk.rows}
        carried = [row for row in carried if row["href"] not in read and _below_hole(row, holes)]
    if carried:
        log(f"  Carrying forward {len(carried)} row(s) of {catalog_id}")
    return [*(walk.rows if walk is not None else []), *carried]


def _spread(value: int) -> int:
    """Put a zero bit between each of the low 16 bits of `value`."""
    value &= 0xFFFF
    value = (value | (value << 8)) & 0x00FF00FF
    value = (value | (value << 4)) & 0x0F0F0F0F
    value = (value | (value << 2)) & 0x33333333
    return (value | (value << 1)) & 0x55555555


def _sort_key(row: Row) -> tuple[int, int, str, str]:
    """Catalog rows first, then collection rows along a Z-order curve.

    The specification asks for a spatial order. The key ends in the registry
    ID and the `href`, so two runs over the same catalogs write the same file.
    """
    bbox = row["bbox"]
    if bbox is None:
        return (0, 0, row[REGISTRY_ID], row["href"])
    # A bbox that crosses the antimeridian sorts at its west edge.
    lon = bbox["xmin"] if bbox["xmin"] > bbox["xmax"] else (bbox["xmin"] + bbox["xmax"]) / 2
    lat = (bbox["ymin"] + bbox["ymax"]) / 2
    x = int((lon + 180.0) / 360.0 * 0xFFFF)
    y = int((lat + 90.0) / 180.0 * 0xFFFF)
    return (1, _spread(x) | (_spread(y) << 1), row[REGISTRY_ID], row["href"])


def build_table(rows: Sequence[Row], *, generated: datetime) -> pa.Table:
    """The index table, sorted, with the GeoParquet metadata."""
    table = pa.Table.from_pylist(sorted(rows, key=_sort_key), schema=SCHEMA)
    geometries = [shapely.from_wkb(wkb) for wkb in table.column(GEOMETRY).to_pylist() if wkb]
    column: dict[str, Any] = {
        "encoding": "WKB",
        "geometry_types": sorted({g.geom_type for g in geometries}),
        # shapely.box writes the exterior ring counterclockwise.
        "orientation": "counterclockwise",
        "covering": {"bbox": {name: ["bbox", name] for name in ("xmin", "ymin", "xmax", "ymax")}},
    }
    if geometries:
        column["bbox"] = [float(v) for v in shapely.total_bounds(geometries)]
    geo = {"version": GEOPARQUET_VERSION, "primary_column": GEOMETRY, "columns": {GEOMETRY: column}}
    metadata = {
        b"geo": json.dumps(geo).encode(),
        b"portolan_registry": json.dumps({"generated": generated.isoformat()}).encode(),
    }
    return table.replace_schema_metadata(metadata)


def read_index(path: Path) -> pa.Table | None:
    """The index at `path`. None when it is missing, unreadable, or in another layout.

    The layout is experimental. A file that an older build wrote can have
    other columns, and no code here can carry rows out of it.
    """
    if not path.is_file():
        return None
    try:
        table = pq.read_table(path)
    except (pa.ArrowException, OSError) as e:
        log(f"  Warning: cannot read {path}: {e}")
        return None
    if not table.schema.equals(SCHEMA, check_metadata=False):
        log(f"  Warning: {path} does not have the columns of this build")
        return None
    return table


def same_rows(table: pa.Table, previous: pa.Table | None) -> bool:
    """True when both tables hold the same rows. The `generated` time does not count."""
    return previous is not None and table.equals(previous, check_metadata=False)


def write_index(table: pa.Table, path: Path) -> None:
    """Write the index as one row group, as the specification asks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd", row_group_size=max(table.num_rows, 1))


def data_link(base_url: str) -> dict[str, str]:
    """The link from the registry catalog to its index.

    A client finds the index by `rel` and `type` together, not by its name.
    """
    return {
        "rel": "data",
        "href": f"{base_url}{INDEX_NAME}",
        "type": MEDIA_TYPE,
        "title": "Catalogs and collections as GeoParquet",
    }
