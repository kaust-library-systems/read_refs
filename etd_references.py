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
from typing import NoReturn


# --------------------------------------------------------------------------
# Stage 0: locate the References section
# --------------------------------------------------------------------------

# Matches ATX headings whose text is (only) something reference-shaped.
# PDF-to-Markdown conversion frequently drops/duplicates letters (e.g. the
# real-world typo "REFERNCES" is missing the 2nd 'E'), so rather than
# spelling out every typo we match a short fuzzy stem: "REFER" followed
# within a few characters by "NCE" (covers REFERENCES, REFERNCES,
# REFRENCES, REFERENCE, etc.) and bibliography-style alternates.
#
# The match is anchored at both ends -- the heading must be *just* the
# reference word: optionally paired with the other word as "Bibliography/
# References", and optionally trailed by a colon, a page number, and the dot
# leaders a table-of-contents line rendered as a heading carries. Without the
# tail anchor the fuzzy stem also swallows unrelated headings like "Reference
# States" or "Reference Frame".
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Heading text has its whitespace (and "+", used as a word separator by some
# conversions) stripped out before this is applied, so alternates need no
# inter-word gaps. The fuzzy stems tolerate letters the conversion drops near
# the front ("REFRENCES", "REEFERENCE") or in the middle ("BIBLIOGRAPY").
_REF_WORD = r"RE\w?FER\w{0,3}NCES?"
_BIB_WORD = r"BIBLIOGRAPH?Y"
_REF_CORE = rf"(?:{_REF_WORD}|{_BIB_WORD}|WORKS\s*CITED|LITERATURE\s*CITED)"
# Adjectives a heading puts in front of the core word ("OVERALL REFERENCES",
# "Uncategorized References" -- the latter an EndNote export artifact).
_REF_ADJ = (
    r"(?:OVERALL|SELECTED|COMPLETE|FULL|ADDITIONAL|UNCATEGORIZED"
    r"|CONSOLIDATED|COMBINED|MAIN|GENERAL|PRIMARY|KEY)"
)
_REF_PHRASE = (
    r"(?:LISTOF)?"
    rf"(?:{_REF_ADJ})*{_REF_CORE}"
    r"(?:/?(?:LIST|CITED))?"
)
REFERENCES_HEADING_TEXT_RE = re.compile(
    rf"^{_REF_PHRASE}"
    rf"(?:/?(?:{_REF_ADJ})*{_REF_PHRASE})?"  # doubled / slash-joined
    # The digits are optional as a group (\d{1,4} inside it), not \d{0,4}: with
    # \d{0,4} the two punctuation runs can split "...." between them in
    # every possible way, so a non-matching heading like "REFERENCES" + 16000
    # dots + "x" took quadratic time to reject.
    r'[\s.:;\'"()]*(?:\d{1,4}[\s.:;\'"()]*)?$',
    re.IGNORECASE,
)

# A leading chapter/section label the heading often carries in per-chapter
# bibliographies, e.g. "## 2.8 REFERENCES", "## Chapter 5: References",
# "## 9 - References", "## VI. Bibliography", "## 7.1 Chapter 1 references",
# "## 6.BIBLIOGRAPHY", "## APENDIX B: References", "## Supplementary
# references". Stripped (up to twice) before matching so the anchored
# REFERENCES_HEADING_TEXT_RE still applies.
HEADING_LABEL_PREFIX_RE = re.compile(
    r"^(?:"
    r"(?:chapter|app?endix|part|section|annex)\s+[\w.]+"
    r"|supplement\w*|additional"
    r"|[IVXLC]{1,7}"
    r"|\d+(?:\.\d+)*"
    r")(?:[.:)]\s*|\s+)[-–—]?\s*",
    re.IGNORECASE,
)


def _normalize_heading_text(heading_body: str) -> str:
    """
    Prepare heading text for matching:

    * drop one or two leading chapter/section labels ("2.8", "Chapter 5:",
      "VI.", "7.1 Chapter 1");
    * strip out internal whitespace and "+" (word separators the conversion
      emits), so "BIBLIOGRAP\tHY" and "+BIBLIOGRAPHY+" still match;
    * collapse an immediately-repeated heading ("BIBLIOGRAPHYBIBLIOGRAPHY" ->
      "BIBLIOGRAPHY"), another duplication the conversion emits (10754_136731).
    """
    text = HEADING_LABEL_PREFIX_RE.sub("", heading_body.strip(), count=1)
    text = HEADING_LABEL_PREFIX_RE.sub("", text.strip(), count=1)
    text = re.sub(r"[\s+]+", "", text)
    half = len(text) // 2
    if half and text[:half] == text[half:]:
        text = text[:half]
    return text


def _section_body(lines: list[str], start_idx: int, heading_level: int) -> str:
    """Text from start_idx up to the next heading of equal-or-shallower level.

    A heading with no letter or digit in it -- "## \\_\\_\\_\\_", "## ==="; a
    horizontal rule the conversion rendered as a heading -- does not end the
    section (10754_322232).
    """
    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        hm = HEADING_RE.match(lines[i])
        if (
            hm
            and len(hm.group(1)) <= heading_level
            and re.search(r"[^\W_]", hm.group(2))
        ):
            end_idx = i
            break
        if _REPORT_TAIL_RE.match(lines[i]):
            end_idx = i  # a Turnitin report / bare page dump got appended
            break
    return "\n".join(lines[start_idx:end_idx]).strip()


# The reference section is often the last real section, so a plagiarism report
# or a bare "PAGE 1 / PAGE 2 / ..." page dump with no heading gets swept in
# after it (10754_273076, 10754_583278). Cut the section at the first such line.
#
# The optional "|" (a table-cell border) is followed by its own whitespace
# inside the group, rather than written as \s*\|?\s*: when there is no pipe, the
# two \s* runs overlap, so a line of 16000 spaces + "x" took quadratic time to
# reject.
_REPORT_TAIL_RE = re.compile(
    r"^\s*(?:\|\s*)?(?:"
    r"ORIGINALITY\s+REPORT|SIMILARITY\s+INDEX|SIMILARITY\s+REPORT"
    r"|FINAL\s*GRADE|GENERAL\s*COMMENTS"
    r"|PAGE\s?\d{1,4}"
    r")\s*(?:\|\s*)?$",
    re.IGNORECASE,
)


def extract_references_section(markdown_text: str) -> str:
    """
    Return the raw text of the References section(s): everything after a
    reference-shaped heading, up to the next heading of equal-or-shallower
    level (or EOF).

    Theses with per-chapter bibliographies have more than one such section;
    all are returned, concatenated in document order. A heading whose body is
    empty is skipped -- that is usually a table-of-contents line rendered as a
    heading (e.g. "## BIBLIOGRAPHY 77") or a duplicated heading, not the real
    section. Returns "" if no non-empty references section is found.
    """
    lines = markdown_text.splitlines()

    bodies = []
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        if not REFERENCES_HEADING_TEXT_RE.match(_normalize_heading_text(m.group(2))):
            continue
        body = _section_body(lines, i + 1, len(m.group(1)))
        if body:
            bodies.append(body)

    return "\n\n".join(bodies)


# --------------------------------------------------------------------------
# Stage 1: segment the section into individual reference entries
# --------------------------------------------------------------------------
#
# Conversions render the reference list in wildly different shapes -- numbered
# or plain bullets, bare "1." lines, "[1]"/"(1)" markers (on their own lines or
# strung along one unbroken line), a Markdown table, or nothing but a blank
# line between author-first entries. segment_entries() runs the recognizers in
# turn -- _segment_table, _segment_marked_lines, _segment_inline_marked,
# _segment_unmarked -- and takes the first that produces a plausible result;
# each returns [] when the section clearly isn't its shape.

# A line that is *only* a number (typically a leaked PDF page footer/header
# that ended up on its own line, e.g. "69").
STRAY_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")

# Leading entry enumerator at the start of a line, in the forms seen across
# conversions: "- 12", "- [12]", "[12]", "12.", "12)", and "2. 12" (a leaked
# chapter number in front of the real one). Requiring whitespace right after
# the enumerator keeps a DOI ("10.1234/..") from matching.
_LINE_ENUMERATOR_RE = re.compile(
    r"^\s*(?:"
    r"\d{1,3}[.)]\s+(\d{1,4})"
    r"|-\s*[\[(]?(\d{1,4})[\])]?[.)]?"
    r"|[\[(](\d{1,4})[\])]"
    r"|(\d{1,4})[.)]"
    r")(?=\s)\s*"
)

# How far ahead of the running count an enumerator may jump and still be
# taken as the next entry (conversions drop the odd marker); a larger jump is
# a stray number inside the previous entry.
_ENUMERATOR_LOOKAHEAD = 20

# An entry marker -- "[n]", "(n)" or "n." -- anywhere in the text, for run-on
# lists that never break into lines ("[1] Farnetti .. [2] Sanfilippo ..", or
# "1. Narayan .. 2. Dunn .."). Only accepted when the numbers turn out to run
# near-consecutively (see _segment_inline_marked).
_INLINE_MARKER_RE = re.compile(
    r'(?:^|(?<=\s))[\[(]?(\d{1,3})(?:[\])]|[.)])\s+(?=["“\'A-Z])'
)

# A plain bullet with no number, e.g. "- Author, Title...".
_BULLET_RE = re.compile(r"^\s*[-*•·‣▪]\s+")

# Markdown table rows: a row of "|"-separated cells, and the "|---|---|"
# separator line under the header.
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^[\s|:-]+$")
_TABLE_ENUMERATOR_RE = re.compile(r"^[\[(]?\d{1,4}[\])]?[.)]?$")

_YEAR_RE = re.compile(r"\b(?:1[89]\d\d|20\d\d)\b")

# Boundary between two "Surname, I., ... YEAR. Title." entries that got glued
# together: whitespace after a sentence/number, then a capitalised surname
# that is itself followed by an initial, a second name, or "et al.".
_AUTHOR_BOUNDARY_RE = re.compile(
    r"(?<=[.)\d])\s+"
    r"(?=[A-Z][A-Za-zÀ-ɏ.'\- ]{1,30}?,\s"
    r"(?:[A-Z]\.(?:[ -][A-Z]\.)*|[A-Z][a-z]+|et\sal\.))"
)

# Non-breaking / thin spaces and soft hyphens the PDF text layer leaves behind.
_UNICODE_SPACE = {
    0xA0: " ",
    0x2007: " ",
    0x2009: " ",
    0x200A: " ",
    0x202F: " ",
    0x2028: "\n",
    0x200B: "",
}

_MIN_ENTRIES = 3  # a thesis reference list always has at least this many


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace left over from PDF column justification."""
    return re.sub(r"\s+", " ", text).strip()


def _clean_section(text: str) -> str:
    text = text.translate(_UNICODE_SPACE)
    # A hyphenation-break soft hyphen often survives as "799-\xad-819".
    return text.replace("-\xad-", "-").replace("\xad", "")


def _looks_like_reference(chunk: str) -> bool:
    chunk = chunk.strip()
    return (
        len(chunk) >= 25
        and (chunk[:1].isalnum() or chunk[:1] in "\"'“")
        and _YEAR_RE.search(chunk) is not None
    )


def _strip_leading_enumerator(chunk: str) -> str:
    """Drop a "1 " / "1. " / "[1] " left at the head of an entry once the list
    has already been split some other way."""
    return re.sub(
        r"^(?:\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}[.)]?)\s+(?=\D)", "", chunk, count=1
    )


def _finalize(numbered: list[tuple[int | None, str]]) -> list[tuple[int, str]]:
    """Attach entry numbers: keep the parsed markers when they are all present
    and unique, otherwise renumber sequentially in document order (per-chapter
    reference lists restart at 1, so their markers collide)."""
    nums = [n for n, _ in numbered]
    if all(n is not None for n in nums) and len(set(nums)) == len(nums):
        return [(n, t) for n, t in numbered]
    return [(i + 1, t) for i, (_, t) in enumerate(numbered)]


def segment_entries(section_text: str) -> list[tuple[int, str]]:
    """Split the references section into (entry_number, raw_text) tuples."""
    text = _clean_section(section_text)
    for recognizer in (
        _segment_table,
        _segment_marked_lines,
        _segment_inline_marked,
        _segment_unmarked,
    ):
        entries = recognizer(text)
        if entries:
            return entries
    return []


def _segment_table(text: str) -> list[tuple[int, str]]:
    """
    The reference list rendered as a Markdown table -- "| 1. | citation |" or
    a single citation column. The enumerator column is often OCR garbage
    ("1)", "(2)", "[3]", "3)") so entries are renumbered sequentially; a row
    with an empty (or non-enumerator) first cell continues the previous entry.
    """
    entries: list[str] = []
    seen_enum = False
    table_lines = other_lines = 0

    for line in text.splitlines():
        if not line.strip():
            continue
        m = _TABLE_ROW_RE.match(line)
        if not m:
            other_lines += 1
            continue
        table_lines += 1
        if _TABLE_SEP_RE.match(line):
            continue

        cells = [c.strip() for c in m.group(1).split("|")]
        enum = cells[0] if _TABLE_ENUMERATOR_RE.match(cells[0]) else None
        content_cells = cells[1:] if (enum is not None or cells[0] == "") else cells
        content_cells = [c for c in content_cells if c]
        if not content_cells:
            continue
        # A wrapped row repeats its text in every column; keep it once.
        content = (
            content_cells[0]
            if len(set(content_cells)) == 1
            else " ".join(content_cells)
        )

        if enum is not None:
            seen_enum = True
        if enum is not None or not seen_enum or not entries:
            entries.append(content)
        else:
            entries[-1] += " " + content

    if len(entries) < _MIN_ENTRIES or table_lines < max(_MIN_ENTRIES, other_lines):
        return []
    entries = [normalize_whitespace(e) for e in entries]
    # Reject a table that isn't a reference list -- e.g. a plagiarism report
    # rendered as a column of "PAGE1", "PAGE2", ... rows (10754_273076).
    reference_shaped = sum(len(e) > 40 or bool(_YEAR_RE.search(e)) for e in entries)
    if reference_shaped < 0.6 * len(entries):
        return []
    return list(enumerate(entries, start=1))


def _segment_marked_lines(text: str) -> list[tuple[int, str]]:
    """
    One entry per line-starting marker -- a bullet ("- ", "• ") or an
    enumerator ("- 1", "[1]", "1.", "2. 11"). Bullets and enumerators are
    often mixed in the same list, and per-chapter / per-letter groups restart
    the count, so an enumerator opens an entry whenever it moves the running
    count forward (small gaps tolerated) or restarts low.
    """
    entries: list[list[str]] = []
    numbers: list[int | None] = []
    bullet_starts = enum_starts = non_blank = 0
    expected = 1

    for line in text.splitlines():
        if STRAY_PAGE_NUMBER_RE.match(line):
            continue
        if not line.strip():
            continue
        non_blank += 1

        bullet = _BULLET_RE.match(line)
        enum = _LINE_ENUMERATOR_RE.match(line)
        num = int(next(g for g in enum.groups() if g is not None)) if enum else None

        if bullet:
            rest = _strip_leading_enumerator(line[bullet.end() :])
            entries.append([rest])
            numbers.append(None)
            bullet_starts += 1
        elif num is not None and (
            expected <= num <= expected + _ENUMERATOR_LOOKAHEAD
            or (num <= 3 and entries)
        ):
            entries.append([line[enum.end() :]])
            numbers.append(num)
            enum_starts += 1
            expected = num + 1
        elif entries:
            entries[-1].append(line.strip())

    starts = bullet_starts + enum_starts
    if len(entries) < _MIN_ENTRIES:
        return []
    # A pure enumerated list must be a mostly-unbroken 1, 2, 3, ... -- a
    # handful of ascending stray numbers (years, volumes) is not a list.
    seq = [n for n in numbers if n is not None]
    if bullet_starts < _MIN_ENTRIES and len(seq) > 1:
        consecutive = sum(b - a == 1 for a, b in zip(seq, seq[1:]))
        if consecutive < 0.6 * (len(seq) - 1):
            return []
    # A few stray line-wrapped hyphens among otherwise unmarked lines.
    if starts < 15 and starts < 0.35 * non_blank:
        return []
    return _finalize(
        [(n, normalize_whitespace(" ".join(p))) for n, p in zip(numbers, entries)]
    )


def _segment_inline_marked(text: str) -> list[tuple[int, str]]:
    """Run-on lists that keep "[n]" / "(n)" / "n." markers but never break into
    lines."""
    parts = _INLINE_MARKER_RE.split(text.strip())  # [pre, n, body, n, body, ...]
    numbers = [int(x) for x in parts[1::2]]
    bodies = parts[2::2]
    if len(numbers) < _MIN_ENTRIES:
        return []

    # The run must be near-consecutive; it may start above 1 when the first
    # entries fell on an earlier, uncaptured page.
    steps = sum(b - a == 1 for a, b in zip(numbers, numbers[1:]))
    if numbers[0] > 30 or steps < 0.8 * (len(numbers) - 1):
        return []
    return _finalize([(n, normalize_whitespace(b)) for n, b in zip(numbers, bodies)])


def _split_glued_entries(chunk: str) -> list[str]:
    """Break a run of several author-first entries that share no separator
    ("..1999. Smith, J., 2001. .."). Returns the chunk unchanged (as a
    one-element list) when it doesn't split into reference-shaped pieces."""
    if len(_YEAR_RE.findall(chunk)) < 3:
        return [chunk]
    parts = [p.strip() for p in _AUTHOR_BOUNDARY_RE.split(chunk) if p.strip()]
    if len(parts) >= _MIN_ENTRIES and sum(
        map(_looks_like_reference, parts)
    ) >= 0.7 * len(parts):
        return parts
    return [chunk]


def _segment_unmarked(text: str) -> list[tuple[int, str]]:
    """
    Entries carrying no enumerator or bullet. They are normally separated by a
    blank line (author-first style); some conversions drop every separator and
    run the whole list together, and some drop only a few, so each blank-line
    chunk is additionally split on glued author-first boundaries.
    """
    chunks = [normalize_whitespace(c) for c in re.split(r"\n[ \t]*\n", text)]

    entries: list[str] = []
    for chunk in chunks:
        if not _looks_like_reference(chunk):
            continue
        for part in _split_glued_entries(chunk):
            part = _strip_leading_enumerator(part)
            if _looks_like_reference(part):
                entries.append(part)

    if len(entries) < _MIN_ENTRIES:
        return []
    return list(enumerate(entries, start=1))


# --------------------------------------------------------------------------
# Stage 2: extract identifiers (DOI / ISBN) for exact-match lookup
# --------------------------------------------------------------------------

# DOIs: standard "10.XXXX/suffix" pattern. Suffix chars per the DOI spec are
# permissive, so we stop at whitespace or a closing bracket/quote. A "(" is
# accepted only together with its matching ")": real DOIs contain balanced
# groups ("10.1016/S1389-5567(03)00026-1"; 2,600+ in the ETD corpus), but a
# lone ")" is usually the parenthesis closing the citation
# ("(doi:10.1002/bies.201100045)."), which must not be swept in. The two
# alternatives start with different characters, so matching stays linear.
DOI_RE = re.compile(r'\b10\.\d{4,9}/(?:[^\s"\'<>()\]}]|\([^\s()]*\))+')

# ISBN-10 or ISBN-13, with or without hyphens/spaces. We require the literal
# "ISBN" prefix when searching (rather than making it optional) -- otherwise
# an unanchored regex search will happily latch onto the first digit run it
# finds (e.g. a publication year) before ever reaching the real identifier.
#
# The number itself has a fixed digit count (10, or 978/979 + 10) and must not
# be followed by another digit. With variable-length digit groups the match
# swallowed whatever came next: "ISBN 019-855370-6 1984" read as 11 digits and
# the ISBN was dropped. Separators may be a hyphen, a space, or both ("ISBN
# 0 7484 0729 -4"), and the prefix may be plural or followed by a space before
# the colon ("ISBNs: 0-471-49799-1", "ISBN : 978-3-540-35305-8").
_ISBN_SEP = r"[- ]{0,2}"
ISBN_RE = re.compile(
    r"ISBNs?(?:-1[03])?\s*(?::\s*)?"
    rf"((?:97[89]{_ISBN_SEP})?\d(?:{_ISBN_SEP}\d){{8}}{_ISBN_SEP}[\dXx])(?!\d)",
    re.IGNORECASE,
)


def clean_doi(raw: str) -> str:
    """Normalise a matched DOI: unescape Markdown underscores, trim trailing junk."""
    # Markdown escapes an underscore: "10.1007/978-1-62703-462-3\_14". The DOI
    # itself has a plain "_", so the escaped form would never match a record.
    doi = raw.replace("\\_", "_")
    # Trailing punctuation often gets swept in: periods, commas, a colon before
    # the title, or a stray line-break backslash ("10.1116/6.0002144.\").
    return doi.rstrip(".,;:\\")


def clean_isbn(raw: str) -> str:
    """Drop hyphens and spaces and upper-case a trailing "x" check digit."""
    return re.sub(r"[- ]", "", raw).upper()


def _isbn10_check_ok(isbn: str) -> bool:
    weights = range(10, 0, -1)
    values = [10 if char == "X" else int(char) for char in isbn]
    return sum(w * v for w, v in zip(weights, values)) % 11 == 0


def _isbn13_check_ok(isbn: str) -> bool:
    weights = [1, 3] * 6 + [1]
    values = [int(char) for char in isbn]
    return sum(w * v for w, v in zip(weights, values)) % 10 == 0


def is_valid_isbn(digits_and_x: str) -> bool:
    """True for a cleaned ISBN-10 or ISBN-13 whose check digit is correct."""
    if re.fullmatch(r"[0-9]{9}[0-9X]", digits_and_x):
        return _isbn10_check_ok(digits_and_x)
    if re.fullmatch(r"[0-9]{13}", digits_and_x):
        return _isbn13_check_ok(digits_and_x)
    return False


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
    """Return (doi, isbn) for one reference entry; None where there is none."""
    doi_match = DOI_RE.search(entry_text)
    doi = clean_doi(doi_match.group(0)) if doi_match else None

    # Try every ISBN-prefixed candidate and keep the first with a valid check
    # digit, so one garbled number does not hide a good one later in the entry.
    isbn = None
    for isbn_match in ISBN_RE.finditer(entry_text):
        candidate = clean_isbn(isbn_match.group(1))
        if is_valid_isbn(candidate):
            isbn = candidate
            break

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


# Exit status: 0 on success, 1 when the document ran but yielded no references
# (no heading, or no entries could be segmented), 2 when the run could not
# happen at all (unreadable input, unwritable output, bad arguments).
EXIT_NO_REFERENCES = 1
EXIT_CANNOT_RUN = 2


def _fail(message: str) -> NoReturn:
    """Print a one-line error to stderr and exit, without a traceback."""
    print(f"etd_references.py: error: {message}", file=sys.stderr)
    sys.exit(EXIT_CANNOT_RUN)


def _is_same_file(a: Path, b: Path) -> bool:
    """True if both paths lead to the same file, following symlinks."""
    try:
        return a.resolve() == b.resolve()
    except (OSError, RuntimeError):  # RuntimeError: symlink loop (Python < 3.13)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("markdown_file", type=Path)
    parser.add_argument("--json", type=Path, help="Write results to this JSON file")
    args = parser.parse_args()

    # Checked before reading: "--json thesis.md thesis.md" would otherwise
    # replace the Markdown source with JSON.
    if args.json and _is_same_file(args.json, args.markdown_file):
        _fail(f"--json would overwrite the input file {args.markdown_file}")

    try:
        text = args.markdown_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"{args.markdown_file} is not valid UTF-8 (bad byte at {exc.start})")
    except OSError as exc:
        _fail(f"cannot read {args.markdown_file}: {exc.strerror or exc}")

    section = extract_references_section(text)
    if not section:
        print("No References-like heading found in this document.", file=sys.stderr)
        sys.exit(EXIT_NO_REFERENCES)

    refs = parse_etd_references(text)

    if not refs:
        print(
            "Found a References heading, but couldn't segment any entries "
            "from it -- the entry format ('- N ...') probably doesn't match "
            "this document. Inspect the raw section below:\n",
            file=sys.stderr,
        )
        print(section, file=sys.stderr)
        sys.exit(EXIT_NO_REFERENCES)

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
        # Serialise first, so a failure here cannot leave a truncated file.
        json_text = json.dumps([asdict(r) for r in refs], indent=2, ensure_ascii=False)
        try:
            args.json.write_text(json_text, encoding="utf-8")
        except OSError as exc:
            _fail(f"cannot write {args.json}: {exc.strerror or exc}")
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
