"""
datacite_inventory.py

Build a read-only inventory mapping KAUST ETDs (local corpus ids) to their
DataCite DOI records, and save a dated snapshot of the raw records.

This is Phase 1 of docs/datacite-update-plan.md. It is strictly read-only:
only GET requests, only to the public DataCite API, no credentials, no
write/PUT/POST/PATCH/DELETE of any kind. The snapshot doubles as the rollback
data for later phases.

Usage:
    python datacite_inventory.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MD_DIR = Path(os.environ.get("ETD_MD_DIR", "/data/exports/etd_md"))
DATACITE_DIR = Path(os.environ.get("ETD_DATACITE_DIR", "/data/exports/etd_datacite"))

API_BASE = "https://api.datacite.org"
DOIS_URL = (
    f"{API_BASE}/dois?client-id=kaust.kaustrepo&resource-type-id=dissertation"
    "&page[size]=500"
)
REQUEST_HEADERS = {"User-Agent": "read_refs-datacite-inventory/1.0"}
REQUEST_TIMEOUT = 30  # seconds
PAGE_DELAY = 1.0  # seconds; be a polite API client between page requests

# The Handle looks like "http://hdl.handle.net/10754/706192" in
# attributes.identifiers, or ".../handle/10754/706192" in attributes.url.
HANDLE_IDENTIFIER_RE = re.compile(r"10754/(\d+)")
HANDLE_URL_RE = re.compile(r"handle/10754/(\d+)")


def fetch_json(url: str) -> dict[str, Any]:
    """GET one page of the DataCite API and return the parsed JSON body."""
    if not url.startswith(f"{API_BASE}/"):
        raise ValueError(f"refusing to fetch a non-DataCite URL: {url}")
    request = urllib.request.Request(url, headers=REQUEST_HEADERS, method="GET")
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        body = response.read()
    return json.loads(body)


def fetch_all_dois(start_url: str) -> list[dict[str, Any]]:
    """Page through the DataCite API, following links.next, and return every
    DOI record found."""
    records: list[dict[str, Any]] = []
    url: str | None = start_url
    first_page = True
    while url:
        if not first_page:
            time.sleep(PAGE_DELAY)
        first_page = False
        page = fetch_json(url)
        records.extend(page.get("data", []))
        url = page.get("links", {}).get("next")
    return records


def handle_id_from_record(record: dict[str, Any]) -> str | None:
    """Return the local ETD id ("10754_<N>") for one DOI record, or None.

    The corpus id is recovered from an ``attributes.identifiers`` entry of
    type "Handle" (preferred), falling back to ``attributes.url``.
    """
    attributes = record.get("attributes", {})
    for identifier in attributes.get("identifiers", []):
        if identifier.get("identifierType") != "Handle":
            continue
        match = HANDLE_IDENTIFIER_RE.search(identifier.get("identifier", ""))
        if match:
            return f"10754_{match.group(1)}"

    match = HANDLE_URL_RE.search(attributes.get("url") or "")
    if match:
        return f"10754_{match.group(1)}"
    return None


def local_etd_ids() -> set[str]:
    """Local ETD ids, matching batch_extract.py's corpus layout."""
    return {p.name for p in MD_DIR.glob("10754_*") if (p / f"{p.name}.md").is_file()}


def group_by_handle(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group DOI records by the local ETD id they map to; drop unmapped ones."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        handle_id = handle_id_from_record(record)
        if handle_id is None:
            continue
        grouped.setdefault(handle_id, []).append(record)
    return grouped


def related_identifier_summary(record: dict[str, Any]) -> dict[str, int]:
    """Count a record's existing relatedIdentifiers by relationType."""
    related = record.get("attributes", {}).get("relatedIdentifiers") or []
    counts: dict[str, int] = {}
    for entry in related:
        relation_type = entry.get("relationType", "unknown")
        counts[relation_type] = counts.get(relation_type, 0) + 1
    return counts


def build_inventory(
    records: list[dict[str, Any]], local_ids: set[str]
) -> dict[str, Any]:
    """Build the derived inventory: single-match records, ambiguous handles,
    and the local/DOI gaps between them."""
    grouped = group_by_handle(records)

    matched: dict[str, Any] = {}
    ambiguous: dict[str, list[str | None]] = {}
    for handle_id, group in grouped.items():
        if len(group) > 1:
            ambiguous[handle_id] = [r.get("attributes", {}).get("doi") for r in group]
            continue
        record = group[0]
        attributes = record.get("attributes", {})
        related = attributes.get("relatedIdentifiers") or []
        matched[handle_id] = {
            "doi": attributes.get("doi"),
            "state": attributes.get("state"),
            "updated": attributes.get("updated"),
            "metadataVersion": attributes.get("metadataVersion"),
            "hasRelatedIdentifiers": bool(related),
            "relationTypes": related_identifier_summary(record),
        }

    mapped_ids = set(matched) | set(ambiguous)
    return {
        "matched": matched,
        "ambiguous": ambiguous,
        "local_without_doi": sorted(local_ids - mapped_ids),
        "doi_without_local": sorted(mapped_ids - local_ids),
    }


def utc_timestamp() -> str:
    """A sortable UTC timestamp for pairing a snapshot with its inventory."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_snapshot(records: list[dict[str, Any]], timestamp: str) -> Path:
    """Save the complete, unmodified DOI records; this is the rollback data
    for later phases."""
    snapshot_dir = DATACITE_DIR / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"{timestamp}.json"
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return path


def write_inventory(inventory: dict[str, Any], timestamp: str) -> Path:
    """Save the derived inventory, named after the snapshot it was built from."""
    DATACITE_DIR.mkdir(parents=True, exist_ok=True)
    path = DATACITE_DIR / f"inventory_{timestamp}.json"
    path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return path


def print_summary(
    records: list[dict[str, Any]],
    inventory: dict[str, Any],
    snapshot_path: Path,
    inventory_path: Path,
) -> None:
    """Print a human-readable summary of the inventory to stdout."""
    matched = inventory["matched"]

    relation_totals: dict[str, int] = {}
    with_related = 0
    for entry in matched.values():
        if not entry["hasRelatedIdentifiers"]:
            continue
        with_related += 1
        for relation_type, count in entry["relationTypes"].items():
            relation_totals[relation_type] = (
                relation_totals.get(relation_type, 0) + count
            )

    print(f"Fetched {len(records)} DOI records from DataCite.")
    print(f"Matched to exactly one local ETD: {len(matched)}")
    print(f"Local ETDs with no DOI: {len(inventory['local_without_doi'])}")
    print(f"DOIs with no local ETD: {len(inventory['doi_without_local'])}")
    print(f"Ambiguous handles (more than one DOI): {len(inventory['ambiguous'])}")
    print(f"Records already carrying relatedIdentifiers: {with_related}")
    for relation_type, count in sorted(relation_totals.items()):
        print(f"  {relation_type}: {count}")
    print(f"\nSnapshot written to {snapshot_path}")
    print(f"Inventory written to {inventory_path}")


def main() -> None:
    """Fetch the KAUST dissertation DOIs, build the inventory, and report it."""
    try:
        records = fetch_all_dois(DOIS_URL)
    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"datacite_inventory.py: error: {exc}", file=sys.stderr)
        sys.exit(1)

    local_ids = local_etd_ids()
    inventory = build_inventory(records, local_ids)
    timestamp = utc_timestamp()

    try:
        snapshot_path = write_snapshot(records, timestamp)
        inventory_path = write_inventory(inventory, timestamp)
    except OSError as exc:
        print(f"datacite_inventory.py: error: {exc.strerror or exc}", file=sys.stderr)
        sys.exit(1)

    print_summary(records, inventory, snapshot_path, inventory_path)


if __name__ == "__main__":
    main()
