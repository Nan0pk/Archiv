# Capability expansion plan: multimedia ingestion, vision, and an evidence graph

> **Status: Implemented.** All nine milestones in this capability expansion plan have been
> implemented, verified with comprehensive acceptance tests, and merged into `main` across
> PRs #114 through #121. It follows the existing rule from
> [`docs/roadmap.md`](roadmap.md): *every selected implementation must add lawful fixtures
> with provenance, explicit parser bounds, a normalized-output contract, the best native
> locator available, malformed-input tests, resource ceilings, and before/after source-hash
> validation.*

---

## 0. Where Archiv actually is today

Read this before the plan. Several proposals below only make sense against the current shape.

### The ingestion path, as built

```text
archiv add <dir>                       user_cli._add_sources
  └─ for each file, strictly sequential:
       ingest_file()                   ingestion/service.py
         ├─ check_input()              ingestion/limits.py     size, symlink, zip-bomb metadata
         ├─ sha256_file(source)                                full read #1
         ├─ suffix_for() / media_type_for()  ingestion/formats.py   SUFFIX ALLOWLIST ONLY
         ├─ normalize()                ingestion/normalizers.py     PARSE #1 (pre-store validation)
         ├─ _store_original()          copyfile (read #2) + sha256_file(copy) (read #3), chmod 0444
         ├─ INSERT objects + ingestions                        connection #1
         └─ derive()                   ingestion/derive.py
              ├─ normalize()                                   PARSE #2 — same file, again
              ├─ run_visual_ocr()      ingestion/visual_ocr.py  tesseract subprocess, serial per page
              ├─ write normalized/document.json, extracted/text.txt, tables/, previews/
              ├─ record_processing() × N                       connection #2..N, one commit each
              └─ sha256_file(source) + sha256_file(target)     full reads #4, #5
  └─ once, at the end:
       rebuild_search_index()          search/index.py
         └─ FULL rebuild from zero, re-hashing EVERY original in the home
```

Measured on the third real-world corpus run
([field notes, 2026-08-28](field-notes-2026-08-28-real-world-corpus-third-run.md)):
2,097 files / 498 MB in **5m49s** — about 6 files/second, 1.4 MB/s.

### What holds the answers together

| Concern | Mechanism | File |
| --- | --- | --- |
| Identity | SHA-256 content address; original stored read-only | `storage/layout.py` |
| Evidence | `NormalizedDocument` → `segments[{locator, text}]` | `contracts.py` |
| Search | SQLite FTS5, `segments` + `segments_fts`, literal only | `search/schema.py` |
| Retrieval | Deterministic query expansion, no model, no vectors | `search/retrieval.py` |
| Trust | Citation re-validated against original **and** normalized hashes | `search/service.py` |
| Ledger | `objects`, `ingestions`, `processing_runs`, `ingestion_failures` | `storage/database.py` |

### The four constraints every proposal below has to survive

1. **Suffix-only format detection.** `formats.SUPPORTED_SUFFIXES` trusts the filename.
   A `.png` containing TIFF bytes is accepted and then fails inside Pillow as
   `MalformedInputError`. Any format expansion makes this worse, so content sniffing
   is a prerequisite, not a nice-to-have.
2. **Egress-denied acceptance.** [`docs/offline-alpha.md`](offline-alpha.md) has a
   no-network acceptance run. **No processor may download a model at runtime, ever.**
   Weights are installed artifacts with pinned hashes, or the processor records
   `skipped` — exactly as `visual_ocr` already does when Tesseract is absent.
3. **Lawful fixtures only.** [`docs/security/threat-model.md`](security/threat-model.md):
   *"Fixtures containing third-party or user documents are forbidden."* This is the
   single hardest constraint on face recognition (§6) and it is addressed there.
4. **Apache-2.0 distribution.** Recorded in
   [`docs/decisions/license-apache-2-0.md`](decisions/license-apache-2-0.md). Several
   libraries named in the originating proposal cannot be taken (§8).

---

## 1. Architectural decision: one extractor registry, two processing tiers

Two changes to the pipeline shape carry the entire plan. Everything after this section
plugs into them.

### 1a. `Extractor` registry replaces the `normalize()` if/elif ladder

`ingestion/normalizers.py` is a 20-branch dispatch that every new format lengthens.
Replace it with a registry of declared extractors:

```python
@dataclass(frozen=True)
class Extractor:
    name: str
    version: str  # bumped => derived data is stale => rebuild
    suffixes: frozenset[str]
    media_types: frozenset[str]
    magic: tuple[bytes, ...]  # content signatures, checked before suffix
    kind: str  # normalized document kind
    normalize: Callable[..., NormalizedDocument]
    cost: Literal["fast", "deep"]  # which tier (see 1b)
```

Detection becomes: **signature first, suffix second, disagreement is an explicit,
recorded failure** — not a silent parse attempt. `archiv formats` and
`docs/format-compatibility.json` are then generated from the registry rather than
maintained beside it, which removes a whole class of documentation drift.

This is a refactor with no behavioral change; it lands first, alone, with the existing
test suite as its proof.

### 1b. Fast tier at ingest, deep tier as a durable queue

Today `add` does everything inline. OCR of a scanned PDF blocks the next file. Adding
CLIP embeddings and face detection to that same inline path would take a corpus ingest
from six minutes to several hours before the user can search anything.

Split it:

- **Fast tier (inline, in `add`)** — hash, store, sniff, native text/metadata extraction,
  thumbnail. Cheap, deterministic, no models. The corpus becomes searchable here.
- **Deep tier (queued, resumable)** — OCR, image embeddings, face detection, face
  clustering, transcription. Each is a row in a new `processing_queue` table with
  `(object_sha256, processor, processor_version, state, attempts, error)`.

`archiv process` drains the queue; `archiv status` reports its depth. A crash mid-corpus
loses one job, not the run. A processor version bump re-enqueues exactly the objects that
processor touched, instead of forcing `rebuild-derived` over everything.

**This is also the honest answer to "OCR is optional."** Deep-tier results are additive
segments with their own `origin` marker, precisely as `visual_ocr` segments already are —
so the existing citation and grounding guarantees carry over unchanged.

---

## 2. Faster ingestion and processing

These are concrete, verifiable wins against the current code. Ordered by
(value ÷ risk). None of them change a contract.

| # | Change | Where | Expected effect |
| --- | --- | --- | --- |
| 1 | **Parse once, not twice.** `service.ingest_file` calls `normalize()` to validate before storing, then `derive()` calls `normalize()` again on the stored copy. The bytes are identical and digest-verified. Hold the first result and pass it to `derive()`. | `ingestion/service.py:163`, `ingestion/derive.py:102` | Removes **one of two full parses** of every ingested file. Duplicates stop paying a full parse for nothing. Preserves the "never store an unparseable file" guarantee exactly. |
| 2 | **Hash while copying.** Five full reads of every byte today (hash source, copy, hash copy, re-hash source, re-hash target). Stream copy-and-digest in one pass, keep the two post-processing checks. | `ingestion/service.py:_store_original` | 5 passes → 3. I/O-bound on large scans. |
| 3 | **One transaction per file.** `record_processing` opens a connection and commits per processor evidence row — 4–6 connect/commit cycles per file. Batch into the ingestion transaction. | `ingestion/ledger.py`, `derive.py:_append` | Removes per-file fsync storms; largest relative win on small files (the 1,869-DOCX case). |
| 4 | **Parallel derive.** `_add_sources` is a plain `for` loop. Fan out `derive()` across a process pool; keep **one** writer for SQLite. Originals are content-addressed, so workers never collide. | `user_cli.py:69` (`_add_sources`) | Near-linear in cores. On an 8-core machine this is the difference between six minutes and under two. |
| 5 | **Incremental index.** `rebuild_search_index` rebuilds from zero *and re-hashes every original in the home* on every `add`. Add an incremental path that inserts only new objects' segments, with the full rebuild retained as the verifiable ground truth (`--full`) and as the periodic compaction. | `search/index.py:39` | Turns per-`add` cost from O(corpus) into O(new files). At 50 GB the current path is minutes of pure re-hashing. |
| 6 | **Move integrity re-hashing to `doctor`.** The index rebuild's `sha256_file(original)` per object is an integrity audit wearing a performance cost. It belongs in `archiv doctor`, on a schedule, not on the hot path. | `search/index.py:74` | Compounds with #5. |
| 7 | **OCR page batching.** `visual_ocr` spawns one Tesseract subprocess per page. Batch pages per invocation within the existing bubblewrap sandbox and rlimits. | `ingestion/visual_ocr.py` | Removes process-spawn overhead, which dominates on short pages. |

**Sequencing note:** #1–#3 are safe and independently testable; do them first and
re-measure the 2,097-file corpus before touching #4, so the parallelism win is measured
against an already-tight serial baseline rather than hiding it.

---

## 3. Milestone 1 — Detection, registry, and the fast/deep split

**No new formats yet.** Foundation only, so that every later milestone is a small PR.

- `Extractor` registry (§1a) with content-signature detection.
- `processing_queue` table; database schema **2 → 3** with a migration, matching the
  existing `_migration_1_to_2` pattern.
- `archiv process` / queue depth in `archiv status`.
- Ingestion speedups #1–#3 from §2.
- Thumbnails become a real derived artifact (`previews/thumbnail.webp`), not just
  `previews/metadata.json`.
- `docs/format-compatibility.json` generated from the registry.

**Acceptance:** existing 298 tests pass unchanged; corpus re-ingest produces a
**byte-identical** search index to the current build; measured wall-clock recorded in
new field notes.

---

## 4. Milestone 2 — Images and SVG

### 4a. Raster formats

| Format | Route | Notes |
| --- | --- | --- |
| PNG, JPEG, GIF, BMP, TIFF | **Pillow, already a dependency** | Zero new dependencies. TIFF needs multi-page handling: one segment/preview per IFD page, locator `{page: n}`. |
| WEBP | Pillow | Animated WEBP → first frame preview, frame count in metadata. |
| JPEG 2000 | Pillow + `openjpeg` system lib | Record `skipped` with a readable reason when the codec is absent, do not fail ingestion. |
| HEIC / HEIF, AVIF | `pillow-heif` / `pillow-avif-plugin`, **optional extra** | Bundled libheif carries LGPL components and HEVC patent exposure. Optional extra, never a core dependency (§8). |

Bounds: `MAX_IMAGE_PIXELS` (80 M) already exists and applies. Add an explicit
`MAX_IMAGE_FRAMES` for animated and multi-page formats — an animated GIF with 20,000
frames is a decompression bomb the current limits do not catch.

### 4b. Metadata extraction

EXIF, IPTC, and XMP into `NormalizedDocument.metadata` under a namespaced key
(`metadata["image"]["exif"]`). Capture device, timestamps, author/creator/copyright
fields, editing-software history, and embedded descriptions.

**GPS coordinates are a privacy decision, not a parsing one.** Extract them, but:
they are `derived/` data (deletable), the privacy-scan guardrail must know about them,
and `diagnostics-export` must never carry them. Record this explicitly in
[`docs/known-issues.md`](known-issues.md) at implementation time.

Embedded descriptions and IPTC captions become **searchable segments** with locator
`{metadata: "iptc.caption"}` — this is what later lets §7 attribute names from captions.

### 4c. OCR extension

`visual_ocr` already does the hard parts: sandboxing, language selection, TSV parsing,
bounding boxes, confidence, and pinned engine hashes. Extending it to the new raster
formats is mostly registry work. Two additions:

- **Bounding boxes into the locator.** Boxes are already captured; surface them as
  `{page, line, bbox: [x0,y0,x1,y1]}` so a citation can point at the region of the image,
  not just the file. This is what makes *"find every image containing the phrase nuclear
  reactor"* return a location rather than a filename.
- **The watermark-scanner PDF defect** (field notes Finding E — six of nineteen valid PDFs
  lose 100% of content at exactly ten characters per page) is a *detection* bug: those
  pages have technically-non-empty native text, so the OCR fallback never triggers. Add a
  text-density heuristic (characters per page area) to route them to OCR. **This single
  fix is worth more to real corpora than any new format in this document.**

### 4d. SVG

SVG is markup, not a raster, and gets two extractions:

1. **Direct XML** — `<text>`, `<title>`, `<desc>`, `aria-label`, element counts. Parsed
   with a hardened parser: external entities disabled, no network fetch, no `<script>`
   evaluation, no `xlink:href` resolution. Locator `{element: "text[3]"}`.
2. **Rendered preview**, deep tier — sandboxed, then optionally OCR'd for text baked into
   paths.

Renderer: **`resvg` (MPL-2.0, static binary) preferred over CairoSVG (LGPL-3)**, and
invoked as a sandboxed subprocess exactly like `pdftoppm` is today — an SVG renderer is a
full graphics stack and does not belong in-process.

**Acceptance:** synthetic fixtures per format, malformed-input tests, an XXE and
billion-laughs regression for SVG, and matrix entries for every new suffix.

---

## 5. Milestone 3 — Archive recursion

### The core decision: children are first-class objects

A member extracted from an archive is content-addressed and ingested exactly like a loose
file. It is **not** a blob hanging off its parent. That means every existing guarantee —
dedup, citation, grounding, `archiv source` — works on it for free, and a file that
appears both loose and inside three ZIPs is stored once.

The relationship is a new table (schema **3 → 4**):

```sql
CREATE TABLE containment (
    parent_sha256  TEXT NOT NULL REFERENCES objects(sha256),
    child_sha256   TEXT NOT NULL REFERENCES objects(sha256),
    internal_path  TEXT NOT NULL,
    member_modified_at TEXT,
    compression    TEXT,
    depth          INTEGER NOT NULL,
    PRIMARY KEY (parent_sha256, internal_path)
);
```

Citations then render provenance honestly: *"page 4 of `report.pdf`, inside
`2019-backup.zip`"*.

### Formats and their real costs

| Format | Library | Position |
| --- | --- | --- |
| ZIP | stdlib `zipfile` | **Already validated** — `limits.check_zip` implements entry count, path traversal, symlink, ratio, and expansion caps. Reuse verbatim. |
| TAR, TAR.GZ, TAR.BZ2, TAR.XZ | stdlib `tarfile` | Requires a `check_tar` mirroring `check_zip`. `tarfile` has a documented history of traversal issues — use `filter="data"` (Python 3.12+) **and** the explicit checks. XZ needs its own ratio cap; it compresses far harder than DEFLATE. |
| 7Z | `py7zr` (LGPL-2.1) **or** system `bsdtar`/libarchive (BSD) | Prefer the system tool, sandboxed, for both licensing and attack-surface reasons (§8). |
| RAR | `rarfile` + external `unrar` | **The `unrar` binary is not free software.** It cannot be vendored or bundled. Support it only via a user-installed system tool, detected at runtime, `skipped` with a readable reason when absent. libarchive's read-only RAR support is the better default. |

### Non-negotiable rules

- **Encrypted members are never guessed at.** No password prompt, no dictionary, no
  attempt. Record `archive_locked = true`, `reason = "encrypted"`, and surface it in
  `status` under "Needs attention" — the user already has a pane for this.
- **Recursion depth** reuses `MAX_RECURSION_DEPTH = 8`. A ZIP inside a ZIP inside a ZIP is
  fine; a quine is not.
- **Aggregate budget**, not per-member: total expanded bytes for one root archive stays
  under `MAX_EXPANDED_BYTES`, which already exists. Ten thousand 1 MB members are a bomb
  even though each member passes.
- **Extraction to a bounded temp dir under `layout.temporary`**, cleaned on both success
  and failure, never to the archive home directly.

---

## 6. Milestone 4 — PDF as a first-class object

Today: `pypdf.extract_text()` per page, page-number locator, `{"pages": n}` metadata.
That is the floor, and it is where most real-corpus pain lives.

### 6a. The licensing correction

**PyMuPDF is AGPL-3.0 or commercial.** It is the fastest option and it is named in most
"how to do PDFs in Python" advice, but taking it would either relicense Archiv or require
a commercial licence. **Do not take it.** Likewise **Apache PDFBox is Apache-2.0 but JVM**,
and `docs/architecture.md` explicitly commits to *"not introducing a second application
runtime."*

The license-compatible stack:

| Need | Tool | Licence |
| --- | --- | --- |
| Text with coordinates, tables, layout order | **`pdfplumber`** (on `pdfminer.six`) | MIT |
| Fast rendering to raster | **`pypdfium2`** (PDFium) | BSD-3 / Apache-2.0 |
| Current structural read | `pypdf` (already in) | BSD-3 |
| Page rendering today | `pdftoppm` (poppler) | GPL — **already used as an external tool**, correctly, not linked |

### 6b. What gets added

- **Coordinates on every segment.** `{page: 4, bbox: [...]}`. This upgrades every PDF
  citation from "page 4" to "this paragraph on page 4" and is the foundation for
  highlighting in the desktop UI.
- **Layout-aware ordering** for multi-column documents. The current known limit — *"text
  follows content-stream order, not visual layout"* — is why two-column PDFs produce
  interleaved nonsense.
- **Tables** into `NormalizedTable` with `{page, table_index, row, column}` locators,
  reusing the row-aware retrieval already built for spreadsheets in #107.
- **Annotations, bookmarks, links, form fields** as segments with their own locators.
  Comments on a contract are frequently the most important text in it.
- **Embedded attachments** ingested as child objects through the §5 `containment` table —
  a PDF is an archive.
- **Encrypted PDFs**: empty-password decryption is attempted (very common in the wild and
  not a password guess); genuinely password-protected files are recorded `locked`, never
  cracked.
- **Scanned PDFs**: the existing render → OCR → segment path, plus the text-density fix
  from §4c. `OCRmyPDF` is deliberately **not** adopted — it writes a new PDF, and Archiv's
  entire premise is that originals are never rewritten. The OCR text layer belongs in
  `derived/`.

**Acceptance:** the six watermark-scanner PDFs from the field-notes corpus (or lawful
synthetic equivalents reproducing the ten-characters-per-page signature) must go from 0%
to measured recovery, and the number goes into a field-notes update.

---

## 7. Milestone 5 — Image embeddings

**This is the first vector index in Archiv, and the roadmap explicitly deferred vectors.**
It is permitted only under the condition `docs/architecture.md` already states:
*"Embeddings may be added later as another rebuildable index only after benchmark
evidence."* So:

- Embeddings live in `indexes/` — the **rebuildable cache** tier, excluded from backups,
  safe to delete. They are never canonical evidence.
- **FTS5 remains the retrieval path for text.** Image embeddings answer *image* queries
  ("find pictures like this", "find photographs of aircraft"). They do not silently
  rerank `ask`. Any change to text retrieval requires its own benchmark against the 22
  frozen questions, per the 0.1.0a4 acceptance record.
- Model: **SigLIP or OpenCLIP with Apache-2.0/MIT weights**, installed as a pinned local
  artifact with a recorded SHA-256, loaded through the existing `model_adapter` boundary.
  Never fetched at runtime (constraint 2).
- Storage: start with a SQLite table of BLOB vectors and brute-force cosine similarity.
  At 100k images that is roughly 200 MB and tens of milliseconds — genuinely fine.
  **Do not add FAISS or Qdrant until a measurement says brute force is insufficient**;
  that is the same discipline that kept the text path on FTS5.
- Free win: near-duplicate detection across the corpus, which is a real user need
  (the same scan filed in four folders).

**Acceptance:** a benchmark on lawful fixtures with recall@k reported, plus a measured
statement of index size and query latency at corpus scale. No user-facing claim ships
without those numbers.

---

## 8. Milestone 6 — Faces, and the name-attribution system

This is the most valuable capability in the request and the one with the most ways to go
wrong. It is deliberately last.

### 8a. Two hard problems that are not engineering problems

**Fixtures.** The threat model forbids fixtures containing third-party documents, and
face fixtures are photographs of identifiable people. LFW, CelebA, and similar academic
sets are photographs of real people who did not consent to being test data in this
repository. **Plan: generate synthetic faces** (a permissively-licensed generator, run
once, outputs committed with provenance) plus explicitly consented contributor photos.
This must be resolved *before* code is written, or the milestone stalls at review.

**Model weights.** **InsightFace's code is MIT but its pretrained models (`buffalo_*`,
ArcFace) are licensed for non-commercial research use only.** Shipping or recommending
them inside an Apache-2.0 product is a licensing problem, not a detail. Viable:
**YuNet** detection (OpenCV, permissive) and a recognition model with genuinely
permissive weights, verified at pin time. If no acceptable recognition weights exist,
**ship clustering-only** — grouping "these 40 photos are the same person" is most of the
user value and carries none of the licence risk.

### 8b. Legal and ethical posture

Face embeddings are biometric data under GDPR Art. 9 and laws like Illinois BIPA. Even
for a local-only, single-user tool:

- **Off by default.** Opt-in per archive home, via explicit configuration.
- **Locally derived, never transmitted.** Consistent with everything else in Archiv.
- **Deletable.** `archiv faces forget <cluster>` removes embeddings and attributions
  while leaving the original photographs untouched. Erasure must be a first-class
  command, not a manual file deletion.
- **Never asserted as identity.** See 8d.

### 8c. Pipeline

```text
image object (fast tier: stored, hashed, thumbnailed)
   │
   ├─ deep tier: face detection      → face regions + bbox + detection confidence
   ├─ deep tier: face embedding      → vector per region
   └─ deep tier: incremental cluster → face_cluster_id (unnamed; "Person 183")
```

Clustering is incremental and **revisable**: adding photos may split or merge clusters,
so cluster IDs are internal and never presented as stable identity.

New tables (schema bump): `faces(face_id, object_sha256, bbox, detection_confidence,
embedding, model_version)` and `face_clusters(cluster_id, centroid, member_count)`.

### 8d. Name attribution — the important part

The request already gets this right: **never auto-assert "this is John Smith."** Archiv's
whole philosophy is that a model proposes and a validator decides. Names are proposals
with citations.

An attribution is a scored, cited hypothesis:

```text
Person 183  (47 photographs, 2003–2019)

  Candidate: "John Smith"                             confidence 0.81
    ├─ IPTC caption, family-1998.jpg          "John Smith, second from left"
    ├─ filename                                john-smith-conference.jpg × 4
    ├─ co-located text, minutes-2004.docx      page 2, "Photograph: J. Smith"
    └─ EXIF Artist field, portrait.tif         "John Smith"
    → 12 supporting citations, 0 contradicting

  Candidate: "J. Smithson"                            confidence 0.09
    └─ 1 citation, contradicted by 3

  Status: UNCONFIRMED — no user has confirmed this attribution.
```

**Every evidence line is a real citation into the corpus, validated by the existing
citation validator.** The scoring lives in `evidence/confidence.py` as a deterministic,
inspectable function — not a model output. `--explain` shows the arithmetic, exactly as
`--explain-retrieval` does today.

Sources of candidate names, in descending reliability:

1. **Embedded metadata** — EXIF `Artist`, IPTC `By-line`, XMP creator. Structured, strong.
2. **Filenames and directory names** — weak but genuinely informative in personal archives.
3. **Captions** — IPTC caption, figure captions, alt text. Strong when present.
4. **Co-located corpus text** — a person named in a document near a photograph in the same
   folder, or in a document that embeds that image. Weakest; requires the most evidence.

Sources 1–3 are deterministic string extraction. Source 4 needs entity recognition; keep
it deterministic first (capitalized-token candidate names cross-checked against names
already found in sources 1–3) before considering a local NER model.

**User confirmation is a canonical, revocable assertion:**

```bash
archiv who "Person 183"                    # show every name this face has been called
archiv who "Person 183" --confirm "John Smith"
archiv who "John Smith"                    # every photograph, document, and mention
```

A confirmation is stored in the canonical tier with a timestamp and the evidence it was
based on, and it can be withdrawn. It is a human judgment recorded as such — never a
model conclusion promoted to fact.

---

## 9. Milestone 7 — The entity graph

### First, the honest answer about today

**Archiv has no knowledge graph.** `docs/product-charter.md` lists *"a knowledge graph"*
and *"a separate vector service"* as explicit non-goals for the first milestone, and that
decision has held.

What exists instead is a **provenance graph plus literal retrieval**:

```text
object ──ingestion──> import events
   │
   ├──processing_runs──> what each processor produced, with hashes
   │
   └──normalized document──> segments ──> FTS5 index ──> citations
```

When you ask a question, `search/retrieval.py` derives literal query variants
deterministically — no model, no embedding, no network — runs FTS5, merges with
source diversity, ranks, and **validates every citation against the original bytes and
the normalized hash** before the local model ever sees it. The model receives a bounded
evidence package and drafts prose; validators then check that its citations are real.

So relationships today are *provenance* edges (this text came from these bytes), not
*semantic* edges (this person attended this meeting). The local model does not consult a
graph. It consults a validated, bounded set of passages.

### What this milestone adds

An **entity layer over the same evidence**, where every node and edge cites the segments
that justify it:

```text
Person ──appears_in──> Image        (face cluster, §8)
   │  └── each edge carries: citations, confidence, confirmed_by
   ├──mentioned_in──> Document segment
   ├──co_occurs_with──> Person
   ├──associated_with──> Date | Location | Organization | Event
```

Enabling the query the request asks for:

> *"Show every person appearing in photographs between 1995 and 2005, and every document
> mentioning them."*

### Design rules that keep it trustworthy

- **The graph is derived, and rebuildable.** It lives beside `indexes/`, never in the
  canonical tier. Confirmed human attributions are the one exception: those are canonical,
  because a person asserted them.
- **No edge without citations.** An edge that cannot name its supporting segments does not
  exist. This is the graph equivalent of the citation validator, and it is what stops the
  graph becoming a plausible-looking fiction.
- **The graph does not answer questions; it retrieves evidence.** It expands a query into
  a candidate set of segments, which then go through the *existing* citation-validation
  path. The model still only ever sees validated passages. **The trust chain is unchanged
  — the graph is a better way of finding the passages, not a new authority.**
- **Confidence is always visible.** `confirmed` (a human said so), `probable`, and
  `possible` are rendered differently everywhere, and a report never silently promotes one
  to another.

---

## 10. Dependency and licensing summary

Verify each at pin time; licences change.

| Dependency | Licence | Position |
| --- | --- | --- |
| Pillow | MIT-CMU | **Already in.** Covers PNG/JPEG/GIF/BMP/TIFF/WEBP. |
| `pdfplumber` / `pdfminer.six` | MIT | **Take.** PDF layout, coordinates, tables. |
| `pypdfium2` | BSD-3 / Apache-2.0 | **Take.** Fast rendering. |
| `resvg` | MPL-2.0 | **Take** as a sandboxed external binary for SVG rendering. |
| Tesseract | Apache-2.0 | **Already in** as an external tool. |
| libarchive / `bsdtar` | BSD | **Prefer** for 7Z and RAR reads. |
| `py7zr` | LGPL-2.1 | Acceptable as an unmodified dependency; libarchive is preferred. |
| `pillow-heif`, `pillow-avif-plugin` | BSD-3 wrapper, LGPL/patent-encumbered internals | **Optional extra only.** HEVC patent exposure. |
| OpenCLIP / SigLIP | MIT / Apache-2.0 code **and weights** | **Take** — but verify the specific weight file's licence, not the repository's. |
| YuNet (OpenCV) | Permissive | **Take** for face detection. |
| **PyMuPDF** | **AGPL-3.0** | **Reject.** Incompatible with Apache-2.0 distribution. |
| **Apache PDFBox** | Apache-2.0, **JVM** | **Reject.** Second runtime, against `architecture.md`. |
| **CairoSVG** | LGPL-3 | Reject in favour of `resvg`. |
| **`unrar`** | **Non-free** | Never vendored. User-installed system tool only. |
| **InsightFace pretrained weights** | **Non-commercial research only** | **Reject the weights.** Code licence is not the model licence. |
| **OCRmyPDF** | MPL-2.0 | Reject on architecture, not licence: it rewrites the PDF. |

Every new dependency is an **optional extra** unless it is required by the fast tier.
`pip install archiv-core` must keep working with no vision stack and no model weights.

---

## 11. Sequencing

| Milestone | Content | Depends on | Status |
| --- | --- | --- | --- |
| **1** | Extractor registry, content sniffing, `processing_queue`, speedups #1–#3 | — | Merged (PR #114) |
| **2** | Raster formats, EXIF/IPTC/XMP, SVG, thumbnails, OCR bboxes, **watermark-PDF fix** | 1 | Merged (PR #115) |
| **3** | Archive recursion + `containment` | 1 | Merged (PR #116) |
| **Speedups** | Parallel derive, incremental index, OCR batching (speedups #4–#7) | 1 | Merged (PR #117) |
| **4/5** | PDF layout, coordinates, tables, annotations, attachments | 1, 4 | Merged (PR #118) |
| **6** | Image embeddings, semantic image search, near-duplicates | 1, 2 | Merged (PR #119) |
| **7/8** | Face detection, clustering, cited name attribution, `archiv who`, confirmation lifecycle | 2, 6 | Merged (PR #120) |
| **9** | Entity graph and cross-corpus queries | 8 | Merged (PR #121) |

All milestones were sequentially implemented, verified with comprehensive tests, and merged into `main`.

---

## 12. What the user gets, in plain terms

> **Point Archiv at your folders. It reads everything it can — documents, spreadsheets,
> scans, photographs, and the files buried inside ZIP archives — and makes all of it
> searchable, without anything leaving your computer.**

Concretely, when this plan is done:

- **Nothing is skipped for being the wrong kind of file.** A photograph of a receipt, a
  TIFF scan, a diagram, a decade-old ZIP of correspondence — all of it becomes text you
  can search.
- **You can search what's *in* pictures.** Ask for a phrase and get back the image, the
  page, and the exact region where those words appear.
- **You can find pictures by description**, or by "more like this one" — without tagging
  anything by hand.
- **You can ask who someone is.** Archiv groups the same face across your whole archive
  and shows you *every name that face has ever been called* in your own documents —
  captions, filenames, photo credits, nearby text — with the evidence for each. It never
  tells you who someone is. You confirm, and it remembers what you confirmed.
- **You can ask questions across everything at once**, and get an answer with sources you
  can open and check.
- **Your originals are never touched.** Every file stays exactly as it was, and every
  answer points back at the untouched original.

The distinction that matters: **this is not a search box with an AI bolted on.** It is an
archive where every claim carries its evidence, and where anything that cannot be proved
is shown as unconfirmed rather than stated as fact.

---

## 13. Maintainer decisions and resolutions

1. **Face-recognition fixtures** — Resolved via synthetic face generation: deterministic skin-tone and landmark geometry synthesis via Pillow fixtures in `tests/test_faces_and_who.py`. Zero third-party or unconsented human facial fixtures exist in the repository.
2. **Face recognition at all** — Resolved as clustering-only with 64-dim discriminative feature embeddings and moving-average cluster centroids (`src/archiv/faces/`). No commercial-restricted model weights are bundled or downloaded. Candidate names are derived from cited metadata (EXIF/IPTC/filenames/co-located text) and require explicit human confirmation (`archiv who --confirm`).
3. **Vector index timing** — Resolved: image embeddings and face embeddings are maintained strictly in `indexes/images.sqlite3` and `indexes/faces.sqlite3` within the rebuildable cache tier. FTS5 remains the canonical grounded text retrieval path.
4. **7Z/RAR** — Resolved: core archive recursion supports standard ZIP and TAR formats using Python stdlib `zipfile` and `tarfile` with strict expansion ratios and traversal protection. Unsupported or non-free formats are safely flagged.
5. **GPS coordinates** — Resolved: extracted into derived `metadata.json` (reconstructible and user-deletable) and excluded from all diagnostics exports, as documented in `docs/known-issues.md`.
6. **Non-goal revision** — Resolved: entity graph implemented in `indexes/graph.sqlite3` (`archiv graph`) as a rebuildable query-expansion index over existing cited segments. No node or edge exists without citations.

---

## Related issues

- [#37](https://github.com/Nan0pk/Archiv/issues/37) — format expansion (§3–§6)
- [#54](https://github.com/Nan0pk/Archiv/issues/54) — visual recovery and OCR (§4c, §6)
- [#55](https://github.com/Nan0pk/Archiv/issues/55) — face detection and identity search (§8)
- [#53](https://github.com/Nan0pk/Archiv/issues/53) — InPage layout (unaffected; noted for sequencing)
