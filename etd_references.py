"""
etd_references.py

Extract the References section from an ETD Markdown file (converted from PDF),
split it into individual entries, and pull out DOI/ISBN identifiers for
exact-match lookup against a library collection.

Usage:
    python etd_references.py path/to/thesis.md
    python etd_references.py path/to/thesis.md --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


# --------------------------------------------------------------------------
# Stage 0: locate the References section
# --------------------------------------------------------------------------

# Matches ATX headings whose text starts with something reference-shaped.
# PDF-to-Markdown conversion frequently drops/duplicates letters (e.g. the
# real-world typo "REFERNCES" is missing the 2nd 'E'), so rather than
# spelling out every typo we match a short fuzzy stem: "REFER" followed
# within a few characters by "NCE" (covers REFERENCES, REFERNCES,
# REFRENCES, REFERENCE, etc.) and bibliography-style alternates.
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')

REFERENCES_HEADING_TEXT_RE = re.compile(
    r'^REFER\w{0,3}NCES?\b|^BIBLIOGRAPHY\b|^WORKS\s+CITED\b',
    re.IGNORECASE,
)


def extract_references_section(markdown_text: str) -> str:
    """
    Return the raw text of the References section (everything after the
    heading, up to the next heading of equal-or-shallower level, or EOF).
    Returns "" if no references heading is found.
    """
    lines = markdown_text.splitlines()

    start_idx = None
    section_level = None

    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and REFERENCES_HEADING_TEXT_RE.match(m.group(2).strip()):
            start_idx = i + 1  # body starts on the next line
            section_level = len(m.group(1))
            break

    if start_idx is None:
        return ""

    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        hm = HEADING_RE.match(lines[i])
        if hm and len(hm.group(1)) <= section_level:
            end_idx = i
            break

    return "\n".join(lines[start_idx:end_idx]).strip()


# --------------------------------------------------------------------------
# Stage 1: segment the section into individual reference entries
# --------------------------------------------------------------------------

# Matches the start of a new entry: "- 12 " (dash, whitespace, number, space).
# Anchored to start-of-line; tolerant of leading whitespace.
ENTRY_START_RE = re.compile(r'^\s*-\s*(\d+)\s+')

# Fallback: a plain bullet with no leading number, e.g. "- Author, Title...".
PLAIN_BULLET_RE = re.compile(r'^\s*-\s+')

# A line that is *only* a number (typically a leaked PDF page footer/header
# that ended up on its own line, e.g. "69").
STRAY_PAGE_NUMBER_RE = re.compile(r'^\s*\d{1,4}\s*$')


def segment_entries(section_text: str) -> list[tuple[int, str]]:
    """
    Split the references section into (entry_number, raw_text) tuples.

    Tries the numbered "- N ..." pattern first (the format seen in KAUST
    ETD conversions so far). If no lines match that pattern at all, falls
    back to treating every plain "- ..." bullet as one entry, numbering
    them sequentially in document order.
    """
    if ENTRY_START_RE.search(section_text):
        return _segment_numbered(section_text)
    if PLAIN_BULLET_RE.search(section_text):
        return _segment_plain_bullets(section_text)
    return []


def _segment_numbered(section_text: str) -> list[tuple[int, str]]:
    entries: list[tuple[int, list[str]]] = []

    for line in section_text.splitlines():
        if STRAY_PAGE_NUMBER_RE.match(line):
            continue  # drop page-footer noise

        m = ENTRY_START_RE.match(line)
        if m:
            num = int(m.group(1))
            remainder = line[m.end():]
            entries.append((num, [remainder]))
        else:
            if not line.strip():
                continue  # skip blank lines
            if entries:
                entries[-1][1].append(line.strip())
            # else: text before the first recognized entry marker -- ignore

    return [(num, normalize_whitespace(" ".join(parts))) for num, parts in entries]


def _segment_plain_bullets(section_text: str) -> list[tuple[int, str]]:
    entries: list[list[str]] = []

    for line in section_text.splitlines():
        if STRAY_PAGE_NUMBER_RE.match(line):
            continue

        m = PLAIN_BULLET_RE.match(line)
        if m:
            entries.append([line[m.end():]])
        else:
            if not line.strip():
                continue
            if entries:
                entries[-1].append(line.strip())

    return [(i + 1, normalize_whitespace(" ".join(parts))) for i, parts in enumerate(entries)]


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace left over from PDF column justification."""
    return re.sub(r'\s+', ' ', text).strip()


# --------------------------------------------------------------------------
# Stage 2: extract identifiers (DOI / ISBN) for exact-match lookup
# --------------------------------------------------------------------------

# DOIs: standard "10.XXXX/suffix" pattern. Suffix chars per the DOI spec are
# permissive, so we stop at whitespace or a closing bracket/paren/quote.
DOI_RE = re.compile(
    r'\b10\.\d{4,9}/[^\s"\'<>)\]}]+',
    re.IGNORECASE,
)

# ISBN-10 or ISBN-13, with or without hyphens/spaces. We require the literal
# "ISBN" prefix when searching (rather than making it optional) -- otherwise
# an unanchored regex search will happily latch onto the first digit run it
# finds (e.g. a publication year) before ever reaching the real identifier.
ISBN_RE = re.compile(
    r'ISBN(?:-1[03])?:?\s*'
    r'((?:97[89][- ]?)?\d{1,5}[- ]?\d{1,7}[- ]?\d{1,7}[- ]?[\dXx])',
    re.IGNORECASE,
)


def clean_doi(raw: str) -> str:
    # Trailing punctuation often gets swept in (periods, commas).
    return raw.rstrip('.,;')


def clean_isbn(raw: str) -> str:
    return re.sub(r'[- ]', '', raw).upper()


def is_plausible_isbn(digits_and_x: str) -> bool:
    return len(digits_and_x) in (10, 13)


@dataclass
class Reference:
    number: int
    raw_text: str
    doi: str | None = None
    isbn: str | None = None

    @property
    def has_identifier(self) -> bool:
        return bool(self.doi or self.isbn)


def extract_identifiers(entry_text: str) -> tuple[str | None, str | None]:
    doi_match = DOI_RE.search(entry_text)
    doi = clean_doi(doi_match.group(0)) if doi_match else None

    isbn = None
    isbn_match = ISBN_RE.search(entry_text)
    if isbn_match:
        candidate = clean_isbn(isbn_match.group(1))
        if is_plausible_isbn(candidate):
            isbn = candidate

    return doi, isbn


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def parse_etd_references(markdown_text: str) -> list[Reference]:
    section = extract_references_section(markdown_text)
    if not section:
        return []

    references = []
    for num, raw_text in segment_entries(section):
        doi, isbn = extract_identifiers(raw_text)
        references.append(Reference(number=num, raw_text=raw_text, doi=doi, isbn=isbn))

    return references


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown_file", type=Path)
    parser.add_argument("--json", type=Path, help="Write results to this JSON file")
    args = parser.parse_args()

    text = args.markdown_file.read_text(encoding="utf-8")

    section = extract_references_section(text)
    if not section:
        print("No References-like heading found in this document.", file=sys.stderr)
        sys.exit(1)

    refs = parse_etd_references(text)

    if not refs:
        print(
            "Found a References heading, but couldn't segment any entries "
            "from it -- the entry format ('- N ...') probably doesn't match "
            "this document. Inspect the raw section below:\n",
            file=sys.stderr,
        )
        print(section, file=sys.stderr)
        sys.exit(1)

    with_id = [r for r in refs if r.has_identifier]
    print(f"Parsed {len(refs)} reference(s); {len(with_id)} with a DOI/ISBN.\n")

    for r in refs:
        tag = []
        if r.doi:
            tag.append(f"DOI:{r.doi}")
        if r.isbn:
            tag.append(f"ISBN:{r.isbn}")
        tag_str = f"  [{', '.join(tag)}]" if tag else "  [no identifier]"
        preview = r.raw_text[:90] + ("..." if len(r.raw_text) > 90 else "")
        print(f"{r.number:>3}.{tag_str} {preview}")

    if args.json:
        args.json.write_text(
            json.dumps([asdict(r) for r in refs], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
