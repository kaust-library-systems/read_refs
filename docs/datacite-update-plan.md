# Plan: adding ETD references to DataCite records

Status: **draft, nothing has been written to DataCite.** The facts below were
gathered on 2026-09-20 with read-only requests to the public DataCite API.
Counts are a snapshot and will drift as new ETDs are added.

## Purpose

`read_refs` extracts the reference lists of KAUST ETDs. The aim is to feed the
extracted references back to DataCite, so that the impact of ETDs is captured
and the metadata can be reused by others. DataCite suggested doing this by
updating each ETD's DOI metadata with `relatedIdentifiers`, using the DataCite
REST API.

Because these are public, institutional persistent-identifier records, the
priority is not to damage existing entries. Every step below is designed so
that it can be checked before it is applied and undone after it.

## Does the suggestion hold up?

Yes. DataCite's documentation confirms it:

- References are added as `relatedIdentifiers`. Each entry needs a
  `relatedIdentifierType` and a `relationType`.
- For references from a record to other works, `Cites`, `References` and
  `IsSupplementedBy` receive the same processing. No single one is
  recommended.
- An existing record is updated with `PUT /dois/{doi}` and a JSON:API body. A
  separate test environment exists.

Things the email does not mention, and that shape this plan:

1. **Arrays are replaced whole.** A `PUT` changes only the attributes sent,
   but for an array such as `relatedIdentifiers` "the entire property will be
   replaced with the contents of what is sent". Every update must therefore
   send the existing entries plus the new ones.
2. **Only DOI-to-DOI links are counted as citations**, according to the
   contributing-citations page. Other identifier types, such as ISBN, can be
   stored but do not create citation events. To be confirmed with DataCite.
3. **DataCite does not validate or de-duplicate for us.** The documentation has
   no guidance on either, and production already holds entries with no
   identifier value (see below).

## What is known about the KAUST records

### Records and mapping

- One DataCite repository holds the ETDs: `kaust.kaustrepo` ("KAUST Research
  Repository"), prefix `10.25781`.
- It has 3,214 dissertation DOIs, all `findable`. The DOI suffixes are random
  (for example `10.25781/kaust-ip46p`), so a DOI cannot be derived from an ETD
  id.
- Each record carries the Handle in `attributes.identifiers` and
  `attributes.url`, for example `.../handle/10754/711748`. That maps to the
  corpus id `10754_711748`.
- 3,058 of the 3,195 ETD files in the corpus map to a DOI. 137 local ETDs have
  no DOI, and 154 DOIs have no local file.
- Two handles have two DOIs each (`10754_693260`, `10754_699938`). They are
  excluded until someone decides which DOI is correct.

### Size of the job

- 1,258 ETDs have at least one extracted reference DOI.
- About 50,850 (ETD, DOI) pairs, 46,294 unique DOIs. Median 11 DOIs per ETD,
  95th percentile 187, maximum 509.
- Only about 15% of references contain a DOI in their text, so most references
  cannot be added by this route. There are also 335 ISBN tags; see the scope
  decision below.
- About 0.8% of the extracted DOIs (415) look malformed: URL or file names
  inside them, trailing punctuation, or non-ASCII characters. This is why
  validation is a gate and not an afterthought.

### Existing `relatedIdentifiers`

12 records already carry them, and they must be preserved exactly:

- 6 records have `IsSupplementedBy` links to datasets.
- 6 records have `References`, added in an earlier pilot in 2023
  (`10.25781/kaust-y87mb` with 136 entries, `kaust-ih30e`, `kaust-8m254`,
  `kaust-mppuc`, `kaust-5fiqu`, `kaust-73m52`).

The pilot found far more DOIs than text extraction can: for ETD `10754_626274`
DataCite holds 132 reference DOIs, while extraction finds none, because that
thesis prints its references without DOIs. The pilot must therefore have
resolved free-text references some other way. How it was done is not recorded
in this repository.

The pilot also left problems in production, which shows that DataCite accepts
bad values:

- 8 entries with no identifier value at all (for example
  `{"relationType": "References", "relatedIdentifierType": "DOI"}`).
- A DOI with a stray Chinese character on the end (`10.1063/1.1385342兴`).
- A URL with a trailing period.

These should be fixed, if at all, as a separate task, and not mixed into the
same run as new additions.

### Update history

1,902 of the records were updated on a single day, 2022-06-30, in what looks
like a bulk operation. Since then updates are sparse, about 10 to 15 a month.
The 2023 pilot references have survived to today. What is **not known** is
whether the repository software re-sends a record's full metadata to DataCite
when the record is edited. If it does, references added through the API could
be overwritten. This is the largest open risk and must be answered before any
rollout (Phase 0).

## The plan

Each phase ends with a gate. Nothing moves on until the result has been
reviewed and approved.

### Phase 0: confirmations (no API calls)

- Ask the repository team how DOIs are registered and updated in DataCite,
  whether an edit in the repository re-pushes metadata, and who holds the
  production credentials.
- Ask DataCite:
  - Is there a limit on the number of `relatedIdentifiers` per DOI? Some
    records here would carry several hundred.
  - Confirm that only DOI-to-DOI links count as citations.
  - Confirm that a `PUT` without an `event` leaves the record's state unchanged.
- Decide the scope (see "Decisions needed").

### Phase 1: read-only inventory

- Build the Handle-to-DOI map from the DataCite API. No credentials are needed.
- Save the complete JSON of every dissertation record as a dated snapshot in a
  permanent location outside the repository. The snapshots are also the
  rollback data.

### Phase 2: build and validate candidates offline

- Normalise each DOI (lower case, trimmed) and de-duplicate it against the
  entries the record already has.
- Apply hard filters: the DOI shape, no URL or file-name fragments, balanced
  parentheses, ASCII only, no trailing punctuation.
- Check that each DOI exists, using a DOI registry lookup. Cache the results
  and exclude any that do not resolve. Exclude an ETD's own DOI.
- For each record, produce the full proposed `relatedIdentifiers` array: the
  existing entries byte for byte, then the new ones.
- Produce a review report (per-ETD counts, exclusions, and reasons). Check a
  random sample of pairs against the source reference text, with a precision
  target agreed in advance.

Gate: sample precision meets the target and the exclusion report is reviewed.

### Phase 3: rehearsal in the test environment

Uses `api.test.datacite.org`. This needs test credentials, which are separate
from production and to be confirmed with DataCite. Test data is not a copy of
production, so create test DOIs that mimic the real shapes: one that already
has `IsSupplementedBy`, one with none, one with several hundred references.

Run the exact production code and check that:

- only `relatedIdentifiers` (and the `updated` and version fields) changed,
- the existing entries are preserved,
- the state is still `findable`,
- running it a second time changes nothing,
- the rollback restores the original array.

Gate: every check passes.

### Phase 4: production pilot on 5 to 10 ETDs

- Hand-pick a mix: no existing entries, existing `IsSupplementedBy`, a large
  reference list, a small one.
- A person reviews the dry-run diff, then the update is applied with the
  explicit write flag.
- Check with a fresh `GET`: every other attribute is unchanged and
  `referenceCount` has risen.
- Wait, and trigger an edit of one of these records in the repository, to
  confirm that nothing overwrites the references.

Gate: no unexpected change, and the references survive a repository edit.

### Phase 5: staged rollout

Batches of 25, then 100, then 500, then the rest, with a pause between
requests. After each batch, verify automatically. The run halts on any
unexpected difference, error, or count mismatch. It is resumable and safe to
repeat.

### Phase 6: monitoring and maintenance

- Keep an append-only log of before and after for every DOI.
- Re-verify periodically.
- Run incrementally for new ETDs, and again when the extraction improves. The
  process is designed to be repeatable.
- Tell DataCite when it is done.

## Safeguards on every write

- **Dry run by default.** Writes need an explicit flag and an allow-list of
  DOIs.
- **Minimal payload.** It contains only `data.type` and
  `attributes.relatedIdentifiers`. The code rejects any other key. In
  particular it must never send `event`, which can change a record's state
  (publish or hide), or `url` or `doi`.
- **Update only, never create.** A `PUT` on a DOI that does not exist creates
  one, so a `GET` must return 200 first. Never use `POST`.
- **Race check.** Fetch the record again immediately before writing. Abort if
  it was updated since the snapshot, or if its existing `relatedIdentifiers`
  differ from the snapshot.
- **Preserve what is there.** Existing entries are kept exactly as they are,
  including malformed ones, and new entries are appended. De-duplicate on the
  identifier (lower case) and its type.
- **Verify after writing.** Fetch the record and compare it with the
  snapshot: everything except `relatedIdentifiers` and the update bookkeeping
  fields must be identical, and the array must be the old entries plus the new
  ones. On any mismatch, restore the saved array automatically.
- **Credentials.** Read from environment variables or a secrets file outside
  the repository. Never log them or commit them. Keep test and production
  credentials separate.
- **Small and steady.** Rate-limited requests, small batches, resumable runs.

## Decisions needed

1. How was the 2023 pilot done, and who holds the production credentials?
2. Does the repository software overwrite DataCite metadata when a record is
   edited? This is the largest unknown.
3. Scope: DOIs only (recommended), or also ISBNs (335) and URLs? ISBNs can be
   stored but do not count as citations.
4. Relation type: `References` (recommended, and what the pilot used) or
   `Cites`. DataCite processes them the same way.
5. Should the existing pilot errors be fixed? If so, as a separate task.
6. What should happen to the two handles with two DOIs, and the 137 ETDs with
   no DOI?

## Beyond DOIs printed in the text

Most references have no DOI in their text, and DataCite accepts only
identifiers. Matching free-text references to DOIs, for example with a
bibliographic search in Crossref, could add many more. It carries a real risk
of wrong matches becoming public citations, so it would need its own
threshold, validation, and sign-off. It is not part of this plan.

## Sources

- [Update DOIs with the REST API](https://support.datacite.org/docs/updating-metadata-with-the-rest-api)
- [Contributing Citations and References](https://support.datacite.org/docs/contributing-citations-and-references)
- [Connecting to Works](https://support.datacite.org/docs/connecting-to-works)
- [Can I add/update DOI metadata with the REST API?](https://support.datacite.org/docs/can-i-addupdate-doi-metadata-with-the-rest-api)
- [RelatedIdentifier, DataCite Metadata Schema 4.6](https://datacite-metadata-schema.readthedocs.io/en/4.6/properties/relatedidentifier/)
- [KAUST metadata dashboard](https://metadata.datacite.org/kaust) (linked in
  the DataCite email; not used for this plan)
