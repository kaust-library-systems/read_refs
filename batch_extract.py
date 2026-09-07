"""
batch_extract.py -- extract references from the ETD corpus, one batch at a time.

The corpus under $ETD_MD_DIR (default /data/exports/etd_md) is sorted by id and
sliced into fixed-size batches. Batches are processed on demand, never all in a
loop.

    uv run batch_extract.py test        # first 25 ids, a format check
    uv run batch_extract.py 01          # ids   [0:100]
    uv run batch_extract.py 02          # ids [100:200]
    uv run batch_extract.py --list      # show every batch and its id range

For each thesis it writes the real deliverable -- one text file per thesis --
to $ETD_REFS_DIR (default /data/exports/etd_refs), named "<id>.txt": a header
comment line, then one reference per line, DOI/ISBN tagged in [brackets].

It also writes an aggregate "batches/batch_<n>.txt" (all the theses in the
batch concatenated) for quick review.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from etd_references import parse_etd_references

MD_DIR = Path(os.environ.get("ETD_MD_DIR", "/data/exports/etd_md"))
REFS_DIR = Path(os.environ.get("ETD_REFS_DIR", "/data/exports/etd_refs"))
BATCH_DIR = Path(__file__).parent / "batches"
BATCH_SIZE = 100
TEST_SIZE = 25


def all_ids() -> list[str]:
    return sorted(p.name for p in MD_DIR.glob("10754_*") if (p / f"{p.name}.md").is_file())


def batch_slice(name: str, ids: list[str]) -> list[str]:
    if name == "test":
        return ids[:TEST_SIZE]
    n = int(name)
    return ids[(n - 1) * BATCH_SIZE : n * BATCH_SIZE]


def render_one(etd_id: str) -> tuple[str, int, int]:
    """Return (file_text, n_references, n_with_identifier) for one thesis."""
    text = (MD_DIR / etd_id / f"{etd_id}.md").read_text(encoding="utf-8")
    refs = parse_etd_references(text)
    if not refs:
        return f"# {etd_id} -- no references extracted\n", 0, 0

    n_id = sum(r.has_identifier for r in refs)
    lines = [f"# {etd_id} -- {len(refs)} references, {n_id} with DOI/ISBN", ""]
    for r in refs:
        tags = []
        if r.doi:
            tags.append(f"DOI:{r.doi}")
        if r.isbn:
            tags.append(f"ISBN:{r.isbn}")
        tag = f"[{', '.join(tags)}] " if tags else ""
        lines.append(f"{r.number:>4}. {tag}{r.raw_text}")
    return "\n".join(lines) + "\n", len(refs), n_id


def cmd_list(ids: list[str]) -> None:
    n_batches = (len(ids) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"{len(ids)} ETDs, {BATCH_SIZE} per batch -> batch_01 .. batch_{n_batches:02d}")
    print(f"  refs written to: {REFS_DIR}")
    print(f"test        {ids[0]} .. {ids[TEST_SIZE - 1]}")
    for n in range(1, n_batches + 1):
        s = batch_slice(str(n), ids)
        print(f"batch_{n:02d}    {s[0]} .. {s[-1]}  ({len(s)})")


def main() -> None:
    ids = all_ids()

    if "--list" in sys.argv:
        cmd_list(ids)
        return
    if len(sys.argv) < 2:
        sys.exit(__doc__)

    name = sys.argv[1]
    subset = batch_slice(name, ids)
    if not subset:
        sys.exit(f"batch {name!r} is empty")

    REFS_DIR.mkdir(parents=True, exist_ok=True)
    BATCH_DIR.mkdir(exist_ok=True)
    suffix = name if name == "test" else f"{int(name):02d}"

    parts = []
    totals = {"extracted": 0, "empty": 0, "entries": 0, "with_id": 0}
    for etd_id in subset:
        body, n, n_id = render_one(etd_id)
        (REFS_DIR / f"{etd_id}.txt").write_text(body, encoding="utf-8")
        parts.append(body)
        totals["entries"] += n
        totals["with_id"] += n_id
        totals["extracted" if n else "empty"] += 1

    header = (
        f"# batch_{suffix} -- {len(subset)} ETDs ({subset[0]} .. {subset[-1]})\n"
        f"# {totals['extracted']} extracted, {totals['empty']} empty; "
        f"{totals['entries']} references total, {totals['with_id']} with DOI/ISBN\n"
        f"# per-thesis files: {REFS_DIR}\n"
    )
    (BATCH_DIR / f"batch_{suffix}.txt").write_text(
        header + "\n" + "\n\n".join(parts) + "\n", encoding="utf-8"
    )

    print(f"wrote {totals['extracted'] + totals['empty']} files to {REFS_DIR}")
    print(f"review copy: {BATCH_DIR / f'batch_{suffix}.txt'}")
    print(header.strip())


if __name__ == "__main__":
    main()
