"""
run_samples.py

Run etd_references.py over the pinned ETD subset in samples.txt and compare
each result against its expected outcome. Prints a per-file line and a summary,
and exits non-zero if any file diverges from its expectation.

Usage:
    uv run tests/run_samples.py
    uv run tests/run_samples.py -v          # also show parsed/identifier counts
    ETD_MD_DIR=/some/where uv run tests/run_samples.py

This is a coarse regression signal, not a unit test: it tells you which of the
known-good / known-broken sample documents changed category, so an
`expected` value in samples.txt should be updated in the same commit that
fixes (or breaks) a document.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etd_references import extract_references_section, parse_etd_references  # noqa: E402

SAMPLES_FILE = Path(__file__).with_name("samples.txt")
DEFAULT_MD_DIR = Path("/data/exports/etd_md")

VALID_EXPECTED = {"parsed", "no-segment", "no-heading"}


def load_samples(path: Path) -> list[tuple[str, str]]:
    samples = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] not in VALID_EXPECTED:
            raise SystemExit(f"malformed samples line: {raw!r}")
        samples.append((parts[0], parts[1]))
    return samples


def classify(md_text: str) -> tuple[str, int, int]:
    """Return (category, n_entries, n_with_identifier)."""
    if not extract_references_section(md_text):
        return "no-heading", 0, 0
    refs = parse_etd_references(md_text)
    if not refs:
        return "no-segment", 0, 0
    return "parsed", len(refs), sum(1 for r in refs if r.has_identifier)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    md_dir = Path(os.environ.get("ETD_MD_DIR", DEFAULT_MD_DIR))
    samples = load_samples(SAMPLES_FILE)

    ok = 0
    failures: list[str] = []

    for etd_id, expected in samples:
        md_path = md_dir / etd_id / f"{etd_id}.md"
        if not md_path.is_file():
            failures.append(etd_id)
            print(f"MISSING  {etd_id}  ({md_path})")
            continue

        actual, n_entries, n_id = classify(md_path.read_text(encoding="utf-8"))
        match = actual == expected
        ok += match
        if not match:
            failures.append(etd_id)

        status = "ok  " if match else "FAIL"
        detail = (
            f"  {n_entries} entries, {n_id} with id"
            if args.verbose and actual == "parsed"
            else ""
        )
        arrow = "" if match else f"  (expected {expected})"
        print(f"{status} {etd_id}  {actual}{arrow}{detail}")

    print(f"\n{ok}/{len(samples)} matched expectation")
    if failures:
        print(f"diverged: {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
