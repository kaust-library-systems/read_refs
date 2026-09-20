# read_refs

Extract the References section from Electronic Theses and Dissertations (ETDs)
that were converted from PDF to Markdown, split it into individual reference
entries, and pull out DOI and ISBN identifiers for exact-match lookup against a
library collection.

PDF-to-Markdown conversion is noisy. Headings lose letters (`REFERNCES`), page
numbers leak into the text as stray lines, and reference lists come out in many
different shapes. The parser is written to tolerate these defects; each regex
in `etd_references.py` is commented with the real input it was written for.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) for dependency management

```bash
uv sync
```

## Usage

### One thesis

```bash
uv run etd_references.py path/to/thesis.md
uv run etd_references.py path/to/thesis.md --json out.json
```

This prints a summary (how many references were parsed and how many carry a
DOI or ISBN), then one line per reference. With `--json`, the full result is
also written to a file as a list of objects with the fields `number`,
`raw_text`, `doi` and `isbn`.

The command exits with status 1 if no References heading is found, or if a
heading is found but no entries can be split out of it. In the second case the
raw section is printed to stderr so you can see the format that was not
recognised.

### A whole corpus, in batches

`batch_extract.py` runs the parser over a corpus of theses laid out as
`<id>/<id>.md`, in batches of 100 ids taken in sorted order:

```bash
uv run batch_extract.py --list      # show every batch and its id range
uv run batch_extract.py test        # first 25 ids, a quick format check
uv run batch_extract.py 01          # ids 0-99
uv run batch_extract.py 02          # ids 100-199
```

For each thesis it writes `<id>.txt` to the output directory: a header line,
then one reference per line, numbered, with any identifiers in brackets:

```text
# 10754_690149 -- 201 references, 108 with DOI/ISBN

   1. [DOI:10.1016/S1389-5567(03)00026-1] Author, A. (2003) Title ...
   2. Author, B. (1999) A reference with no identifier ...
```

It also writes a combined `batches/batch_<n>.txt` for reviewing a whole batch
at once. The `batches/` directory is not tracked by git.

### Locations

| Purpose | Default | Override |
|---|---|---|
| Input Markdown corpus | `/data/exports/etd_md` | `ETD_MD_DIR` |
| Extracted references | `/data/exports/etd_refs` | `ETD_REFS_DIR` |

## How it works

`parse_etd_references()` runs three stages:

1. **Locate the section.** Find headings that look like "References",
   "Bibliography", "Works Cited" and similar, and take the text up to the next
   heading of the same or a higher level. The match is fuzzy, so it copes with
   dropped or doubled letters, chapter-number prefixes (`## 2.8 REFERENCES`)
   and joined headings (`Bibliography/References`). A thesis with per-chapter
   bibliographies contributes every section it has.
2. **Split into entries.** Try a series of recognisers, each of which gives up
   unless the section clearly matches its shape: numbered or plain bullets,
   `[1]` / `(1)` / `1.` markers, markers run together on one line, Markdown
   tables, and blank-line-separated entries with no markers at all. Leaked page
   numbers are dropped.
3. **Extract identifiers.** Take one DOI and one ISBN per entry.
   - **DOI:** `10.NNNN/...`. Parentheses are kept when balanced, as in
     `10.1016/S1389-5567(03)00026-1`, and left out when they belong to the
     surrounding citation. Markdown-escaped underscores (`\_`) are turned back
     into `_`, and trailing punctuation is trimmed.
   - **ISBN:** must follow the literal word `ISBN`, so a publication year is
     never mistaken for one. It must be a 10- or 13-digit number with a valid
     check digit. Entries with several candidates use the first valid one.

## Limitations

- A DOI that the conversion split with a space (`10.1016/0031-3203(94)
  90140-6`) is cut at the space, so the identifier is incomplete.
- A DOI followed directly by a year in parentheses keeps the year
  (`10.1017/jfm.2012.226(2012)`).
- HTML entities inside a DOI (`&lt;`, `&gt;`) are not decoded.
- An ISBN whose printed check digit is wrong is not reported. That covers about
  1% of the ISBNs in the ETD corpus.
- Theses with no reference section, or with a non-standard one, produce no
  output. The parser reports this instead of guessing.

## Development

```bash
uv run ruff check                 # lint
uv run ruff format                # format
uv run tests/run_samples.py -v    # regression check over a pinned set of ETDs
```

`tests/samples.txt` pins 31 theses from the corpus, each with its expected
outcome (`parsed`, `no-segment` or `no-heading`) and a note on the format
variant it covers. `run_samples.py` reads them from `ETD_MD_DIR` and exits
non-zero if any thesis now parses differently. When a change alters how a
document parses on purpose, update its `expected` value in the same commit.

The sample check needs access to the corpus, so it cannot run without it. There
are no unit tests for the individual stages yet.

Because the recognisers in stage 2 trade off against each other, a change that
fixes one shape can break another. After touching stage 1 or 2, run the sample
check and, if you can, a wider sweep over the corpus.

## License

[Mozilla Public License 2.0](LICENSE)
