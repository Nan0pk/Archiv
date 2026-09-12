# S10A — Owner decision: replace the duplicate method

**Owner decision:** option 3, chosen by the repository owner on 2026-09-12.

## Decision

Archiv will not try to make one image-similarity score answer several different questions.
The current colour-and-edge cosine score is not a safe basis for telling a user that two
files are duplicates.

The duplicate feature will be rebuilt as separate methods for separate jobs:

1. **Exactly the same file** — use Archiv's existing content-hash / successful-ingestion
   evidence. No image similarity is needed.
2. **Same photograph after resize or recompression** — use a perceptual-hash method such
   as pHash or dHash, with thresholds chosen only after generated-fixture measurements
   show that true near-copies and unrelated images are separated.
3. **Same document represented by different files or scans** — compare document evidence,
   beginning with normalized extracted text where Archiv already has it. Scanned pages
   may use OCR text only when an OCR provider actually produced evidence. A later page
   fingerprint or layout method is allowed only if it gets its own measured acceptance
   evidence.
4. **General "looks like this" search** — if Archiv keeps this capability, it is a
   separate visual-search problem. A future local image embedding must use pinned weights
   with a recorded SHA-256 and must never be downloaded at runtime.

## Why

The measurements in `docs/plan/steps/S10A.md` show that the existing score can put clearly
unrelated document pages into the same duplicate group. The proposed spread-based guard
was also disproved: genuine copies can be just as tightly bunched as unrelated pages.
Changing another threshold would therefore create a new guess, not a demonstrated fix.

The owner accepted the recommendation to change the method rather than remove the feature.

## What happens immediately

Until a replacement method has passed its own acceptance measurements:

- `archiv images duplicates` must **refuse to report duplicate groups based on the current
  colour-and-edge score**. The refusal should explain that duplicate detection is being
  rebuilt because the old method cannot safely distinguish some unrelated content.
- `archiv images find-similar --image PATH` may remain as an image-to-image similarity
  surface under the limitations already measured in S10, but its result must not be
  presented or reused as proof that files are duplicates.
- Exact duplicate detection that already comes from content identity remains valid and
  should not be disabled.

This temporary refusal is a safety consequence of option 3, not a separate owner choice.
It prevents the known dangerous output while the replacement is built.

## Follow-up work authorized by this decision

The queue should be split into bounded follow-up steps rather than rebuilding everything
in one change:

1. make the unsafe `images duplicates` surface refuse before it can emit a colour-score
   duplicate claim, with a regression fixture containing unrelated document pages;
2. build and measure perceptual near-duplicate detection for photographs;
3. build and measure document duplicate detection from normalized text, with an explicit
   refusal when required text evidence is unavailable;
4. route the duplicate command to the appropriate proven method and keep unsupported
   cases as refusals rather than guesses;
5. treat general visual similarity as a separate capability, not as duplicate evidence.

Each implementation step must have executable acceptance tests. None may inherit a
threshold merely because it is common elsewhere; the threshold used here must be justified
by this repository's generated fixtures and recorded measurements.

## What this decision does not authorize

- It does not authorize deleting originals or automatically removing any file.
- It does not authorize a runtime model download.
- It does not make the current image score a duplicate detector under a new name.
- It does not claim that perceptual hashing or text comparison is already implemented.
- It does not mark any follow-up implementation step complete.

## Decision source

The owner was presented with the three choices recorded in `docs/plan/steps/S10A.md` and
answered **"3"** on 2026-09-12. This file records that choice; it does not infer or invent
one on the owner's behalf.
