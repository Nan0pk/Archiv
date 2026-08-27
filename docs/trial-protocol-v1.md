# Private local-corpus beta trial protocol (v1.0.0)

**Protocol ID:** `ARCHIV-PRIVATE-TRIAL-1.0.0`  
**Status:** frozen for beta evidence  
**Change rule:** corrections require a new versioned file; completed run records always name the
protocol ID, Archiv commit, corpus-manifest digest, and result-schema version.

## 1. Safety and evidence boundary

Use only material whose owner has given informed consent for this trial, or lawful synthetic
material. Before starting, record consent, permitted operators, retention date, and deletion
procedure **locally**. Never commit those records. Use an encrypted Fedora account, an isolated
`ARCHIV_HOME`, full-disk encryption, and loopback model endpoints. Disable Wi-Fi and other network
interfaces after dependencies and models are installed; verify with `ss -lntup` that the model
listens only on `127.0.0.1`/`::1`. Do not use cloud APIs, telemetry, remote mounts, or model
auto-download during a run.

Raw documents, filenames, paths, prompts, excerpts, answers, model output, manifests, logs, and
per-question results remain inside the private run directory and are deleted at the agreed date.
The only publishable files are the sanitized aggregate described in section 8 and operational
evidence that has passed the privacy scan. A reviewer must inspect both before export.

## 2. Frozen corpus strata

Create three non-overlapping, read-only collections. Record counts and bytes locally, but publish
only the ranges below. Every stratum must contain clean documents, deliberately malformed and
unsupported documents, exact duplicates, a current/superseded revision pair, dense tables,
native-text and scanned PDFs, English plus at least two locally relevant non-English languages
(including one RTL language where consent permits), and documents longer than 100 pages or
100,000 extracted words.

| Collection | Documents | Source bytes | Minimum scored questions |
|---|---:|---:|---:|
| Small | 50-200 | 25 MiB-500 MiB | 30 |
| Medium | 500-2,500 | 0.5-5 GiB | 60 |
| Large | 5,000-20,000 | 5-25 GiB | 100 |

At least 5% of each collection must exercise each applicable stress class; malformed and
unsupported inputs may each use a 1% floor. Build questions before running Archiv. Each question
has a blinded ID, required source revision(s), required facts, forbidden claims, whether
insufficient evidence is correct, and a 1-5 human usefulness rubric. Include at least 10% negative
questions and 10% multi-source questions. A second reviewer checks the gold set against originals.

## 3. Supported Fedora hardware tiers

Run every non-model core cell on all tiers. A tier is eligible only when it runs the supported
Fedora release, has x86-64 hardware, SSD storage with at least twice the corpus size free, and
records CPU model/logical CPUs, RAM, storage type, Fedora/Python/Archiv versions, and power mode.

| Tier | Logical CPUs | RAM | Intended coverage |
|---|---:|---:|---|
| Minimum | 4+ | 8 GiB | Small; medium ingestion/recovery only |
| Recommended | 8+ | 16 GiB | Small and medium; large core run |
| Workstation | 16+ | 32 GiB+ | All collections and model cells |

The documented 16 GiB HP Victus is a recommended-tier target, not a result. If a collection
cannot run because of capacity, record `capacity_blocked`; do not silently shrink it or call it a
failure.

## 4. Bounded local-model matrix

The deterministic no-model path runs for all nine hardware/collection cells. Model-assisted
`ask` and `report` use only documented OpenAI-compatible loopback adapters: Ollama, LocalAI, and
vLLM. Test one instruct model in each size band that the host can load: micro (up to 2B
parameters), small (>2B to 4B), and medium (>4B to 8B). Quantization, context length, model file
SHA-256, runtime/version, prompt template, and thread/GPU settings are frozen locally.

To bound cost, run at most six model configurations per hardware tier: all three bands on Ollama,
then the small band on LocalAI and vLLM, plus one operator-selected repeat for variance. Run every
configuration on Small, and only the best passing configuration on Medium and Large. Unsupported
or unloadable cells are `capacity_blocked`, never substituted. Use temperature 0, fixed seed when
supported, and three repetitions per question. No model result may replace deterministic
validation.

## 5. Run procedure

1. Reboot; stop unrelated services; confirm wall power/performance mode and at least 2x corpus
   free space. Capture sanitized host facts and baseline RSS/disk usage.
2. Hash originals and the private gold manifest. Copy the selected stratum into a disposable,
   encrypted trial directory; never modify originals.
3. Start packet-denied operation and the chosen loopback runtime. Confirm no non-loopback socket.
4. Run `python scripts/run_field_trial.py --corpus PRIVATE_COPY --questions PRIVATE_GOLD.json
   --local-only --output PRIVATE_RUN` (add the explicit loopback endpoint and model name only for
   model cells). Time from command start through committed searchable index.
5. Run every scored query three times after one unscored warm-up. Generate both deterministic and,
   where applicable, model reports. Two blinded reviewers score answer/report usefulness.
6. During a separate recovery pass, terminate ingestion at 25%, 60%, and during index rebuild
   (one interruption per fresh home). Restart the identical command. Verify source hashes,
   database integrity, no duplicate logical records, complete searchable state, and parity with a
   clean-run manifest.
7. Re-hash originals; collect peak process-tree RSS, median/95th/max latency, index and total
   `ARCHIV_HOME` byte growth, and recovery time. Repeat any noisy performance cell three times and
   publish the median.
8. Run the privacy scan, manually review the export, delete the disposable corpus/model evidence
   on schedule, and retain only the approved aggregates.

## 6. Measurements and denominators

- **Ingestion success:** searchable, integrity-valid supported files / supported files attempted.
  Report unsupported, degraded, and failed counts separately by reason; unsupported inputs are
  not ingestion failures. A degraded file is accepted but loses expected text, pages, tables,
  language, or OCR coverage.
- **Indexing time:** elapsed seconds and supported MiB/second from cold start to committed index.
- **Query latency:** warm median, p95, and maximum wall time, separated into retrieval-only,
  `ask`, and report generation.
- **Required-source recall:** questions retrieving every gold source within evidence limit 8 /
  eligible questions; also report macro mean recall. A superseded revision is wrong unless asked.
- **Citation correctness:** claims whose cited passage entails the claim and whose source ID,
  location, revision, and hash validate / all factual cited claims. Report fabricated IDs and
  uncited factual claims separately.
- **Usefulness:** percentage of answers and reports receiving median reviewer score >=4/5, plus
  inter-reviewer disagreement. Reviewers consider correctness, completeness, clarity, honest
  uncertainty, and actionability.
- **Model-specific failures:** schema rejection, timeout, unsupported claim, contradiction miss,
  refusal error, citation mismatch, and report validation failure per model/repetition.
- **Resources:** peak RSS for the Archiv plus child process tree; disk growth is final and peak
  bytes above baseline, separated into originals, derived objects, index, evidence, and model.
- **Recovery:** successful interrupted scenarios / attempted scenarios and seconds to a state
  identical in logical manifest and search outcomes to the clean run.

Use monotonic clocks, byte counts (not filesystem display strings), nearest-rank p95, and the
maximum sampled RSS at <=1-second intervals. Preserve missing values as `null` with a reason.

## 7. Failure triage and regression conversion

Rank failures by: safety/integrity first, then number of affected eligible questions/files, then
user impact, then p95 cost. The highest-ranked reproducible category is the dominant failure.
Before beta reassessment, add a minimal non-sensitive regression fixture. If the source cannot be
redistributed, create a lawful synthetic equivalent that preserves only the triggering structure,
record its provenance, and prove the test fails on the defective revision and passes after repair.
The initial equivalents are catalogued in `tests/fixtures/trial-regressions/manifest.json` for
watermark-only PDF extraction, source crowding, duplicate revisions, malformed containers,
multilingual text, dense tables, and long documents.

## 8. Sanitized aggregate and publication gate

Publish one JSON object per cell containing only: protocol/result versions; pseudonymous run ID;
commit; date; coarse hardware tier; collection band; runtime family/version; model size band and
model digest prefix; attempt counts; aggregate rates/latencies/resources; failure-category counts;
threshold verdict; and evidence-file SHA-256 values. Suppress a category breakdown when its count
is below five. Never publish hostnames, usernames, serials, IPs, exact corpus counts/bytes, source
or model names, paths, questions, answers, excerpts, prompts, raw output, or per-file/per-question
records.

An incomplete matrix is explicitly `NO-GO (insufficient evidence)`. Historical/ad-hoc results
may motivate fixtures but cannot be relabelled as v1 evidence. Beta readiness is decided only by
the thresholds in [the definition of done](definition-of-done.md).

