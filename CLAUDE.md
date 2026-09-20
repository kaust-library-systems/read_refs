# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Extract the "References" / "Bibliography" section from ETD (Electronic Thesis &
Dissertation) Markdown files that were converted from PDF, split it into
individual entries, and pull out DOI/ISBN identifiers for exact-match lookup
against a library collection.

The conversions are noisy: headings lose letters (`REFERNCES`), PDF page
footers leak in as stray number-only lines, and column justification leaves
ragged whitespace. Parsing code is written to tolerate this — see the module
docstrings and regex comments in `etd_references.py` for the specific
real-world defects each pattern defends against.

## Data locations

- Input: `/data/exports/etd_md/` — Markdown files, one per thesis, in subdirectories
- Output: `/data/exports/etd_refs/` — extracted references as text

Any `data/` directory inside the repo is scratch for testing only (see commit
`6fa5904`); real corpora live under `/data/exports/`.

## Commands

```bash
uv run etd_references.py path/to/thesis.md            # print parsed references
uv run etd_references.py path/to/thesis.md --json out.json   # also dump JSON
uv run ruff check                                     # lint
uv run ruff format                                    # format
uv run tests/run_samples.py -v                        # regression check over the pinned ETD subset
```

`main.py` is the unused `uv init` stub — the real entry point is
`etd_references.py`.

### Test subset

`tests/samples.txt` pins 31 ETD IDs from the corpus, each tagged with its
expected outcome (`parsed` / `no-segment` / `no-heading`) and a note on the
format variant it covers. When a change makes a document parse differently,
flip its `expected` in the same commit. `tests/run_samples.py` reads the files
from `$ETD_MD_DIR` (default `/data/exports/etd_md`) and exits non-zero on any
divergence. There are no per-stage unit tests yet.

As of this subset a random 1200-document sweep parses ~98%; the rest are
mostly genuine (no reference section, or non-standard "data sources"
sections). Re-run a sweep after touching Stage 0 or Stage 1 — the recognizers
trade off against each other and a change that fixes one shape can regress
another.

## Architecture

`etd_references.py` is a single-file pipeline. `parse_etd_references(markdown_text)`
orchestrates four stages, each independently testable:

1. **`extract_references_section`** — scan ATX headings for a fuzzy
   reference-heading match (`_normalize_heading_text` strips chapter-number
   prefixes and repairs mangled/duplicated heading text first), and return
   each matching section's body up to the next heading of equal-or-shallower
   level. Multiple sections (per-chapter bibliographies) are concatenated;
   empty bodies (TOC lines rendered as headings) are dropped.
2. **`segment_entries`** — `_clean_section` first normalises unicode spaces and
   soft hyphens, then a chain of recognizers is tried in order, each returning
   `[]` unless the section clearly matches its shape:
   `_segment_marked_lines` (one entry per line-starting bullet or enumerator —
   `- N`, `[N]`, `(N)`, `N.`, `2. 11`; bullets and numbers may be mixed and
   the count may restart), `_segment_inline_marked` (a run-on list that kept
   its `[N]`/`N.` markers on one unbroken line), then `_segment_unmarked`
   (blank-line-separated author-first entries, with glued entries split on
   `_AUTHOR_BOUNDARY_RE`). Entry numbers come from the markers when they are
   unique, else sequential.
3. **`extract_identifiers`** — regex out a DOI (`10.XXXX/...`) and an
   `ISBN`-prefixed identifier; ISBN requires the literal prefix to avoid
   latching onto publication years.
4. Results are collected into `Reference` dataclasses (`number`, `raw_text`,
   `doi`, `isbn`, `has_identifier`).

`main()` adds CLI parsing and human-readable / JSON reporting, and exits
non-zero with a diagnostic dump when the heading is found but no entries
segment (the likely sign of an unhandled entry format).

## Conventions

- Readability over cleverness.
- When adding tolerance for a new conversion defect, comment the pattern with
  the concrete failing input, matching the existing regex comments.
