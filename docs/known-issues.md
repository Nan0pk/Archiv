# Known issues

## Diagnostics export

- Schema `1` reports aggregate counts, not which document failed. This is intentional;
  support should suggest general recovery steps without requesting private source data.
- Platform and package versions may fingerprint an unusual installation, and aggregate
  counts disclose approximate library activity. Review the complete preview before saving.
- A destination must be a new file; the exporter refuses to overwrite an existing file.
- Database corruption is represented only as `unreadable`; raw SQLite errors are withheld.

## Image metadata and GPS coordinates

- EXIF metadata extraction preserves GPS coordinates when present in image headers.
  This extracted metadata is stored strictly within derived artifacts (`previews/metadata.json`)
  which are reconstructible and user-deletable.
- Diagnostics export (`archiv diagnostics-export`) operates strictly on aggregate system
  metrics and never includes document metadata, text segments, or GPS coordinates.

## Offline-alpha `egress-denied` fails intermittently on pull requests

- `tools/install-fedora.sh` resolves `--ref` through
  `https://api.github.com/repos/<repo>/commits/<ref>`. Inside the container built by
  [`.github/offline-alpha.Dockerfile`](../.github/offline-alpha.Dockerfile) that request
  is **unauthenticated**: the build receives only `--build-arg ARCHIV_REF`, no token is
  passed, and `gh` is not installed, so `CURL_AUTH` is empty.
- Unauthenticated GitHub API access is limited to 60 requests per hour **per IP**, shared
  across GitHub-hosted runners. When the limit is hit the API returns `403`, `curl` exits
  22, and the installer feeds the error body to `json.load`, producing
  `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`. Observed on
  [#123](https://github.com/Nan0pk/Archiv/pull/123); a re-run of the same commit passed.
- The obvious remedy — passing a token into the build to raise the limit — conflicts with
  the repository's own trust boundary: [Contributing](../CONTRIBUTING.md) states that
  branch and fork pull requests "must not receive repository secrets", and
  `scripts/audit_ci_trust.py` reports a `pr_secret_reference` violation for exactly that.
- Remaining options, none yet chosen: retry with backoff on `403`; install from the local
  checkout using the installer's existing `--source` flag, accepting that this stops
  exercising the download path a real user takes, which is the purpose of the job; or
  accept the flakiness and re-run. Until one is chosen, treat a `403` here as
  environmental and re-run once before investigating.

General alpha limitations remain documented in [Offline alpha](offline-alpha.md) and the
[format compatibility matrix](format-compatibility.json).

## A `config/model.json` written after S02 is rejected by an earlier Archiv build

- `ModelConfig` gained a `provenance` field (`src/archiv/model_adapter.py`), derived from
  `adapter` and written into `config/model.json` by `save_model_config`.
- Reading is forward-compatible in the direction that matters day to day: a `model.json`
  written *before* this change has no `provenance` key and still loads, taking the derived
  value. `tests/test_model_boundary.py::test_existing_model_json_without_provenance_still_loads`
  covers this.
- The reverse does not hold. `ModelConfig` subclasses `StrictModel`
  (`src/archiv/contracts.py`, `extra="forbid"`), so an earlier build reading a file this
  build wrote fails with `provenance | Extra inputs are not permitted
  [type=extra_forbidden]` rather than ignoring the unknown key.
- This is reachable in practice because `config` is one of `DURABLE_DIRECTORIES`
  (`src/archiv/archive.py`), so `model.json` travels inside portable archives and backups.
  Restoring a new archive into an older installation hits it.
- No remedy is planned. [Architecture](architecture.md) already states Archiv never
  performs an in-place downgrade, so this is consistent with the documented contract
  rather than a departure from it. The recovery is to delete `config/model.json` and
  re-run `archiv model configure`, which loses no canonical data: the file is policy, not
  evidence, and originals are untouched.

## Two `archiv add` runs on one archive can both report a new original

- Whether a candidate is a duplicate is decided in `src/archiv/ingestion/service.py` by
  asking the database whether that content has already been ingested successfully. That
  question is asked in the ordered commit phase, so within one `archiv add` the answer
  does not depend on which parallel worker happened to finish first — which is the race
  this replaced, and which could report every copy of a file as a duplicate of an
  original it never recorded.
- The read is not in the same transaction as the write that follows it. Two separate
  `archiv add` processes running against the same archive at the same time can therefore
  each see no successful ingestion and each record itself as a new original.
- The earlier code closed that window incidentally, because it also consulted whether the
  bytes were already on disk, and the per-digest file lock serialised that. Removing that
  term was necessary to fix the single-process race, so this is a deliberate trade: a
  reproducible fault inside one run — 8 failures in 30 runs on one machine and about 1
  in 10 on another, and it broke the required check on
  [#131](https://github.com/Nan0pk/Archiv/pull/131) — exchanged for a narrower one that
  needs two concurrent runs on the same archive. The rate is a property of the machine,
  not of the bug; both measurements are given so neither reads as the true frequency.
- Consequences are a wrong "new originals" count and some duplicated expansion work. Not
  corruption: originals stay content-addressed, containment rows are written with
  `INSERT OR REPLACE`, and processing evidence is keyed on the digest.
- No remedy is planned yet, because it is not known whether concurrent adds against one
  archive is a scenario Archiv intends to support, and no test covers it. Deciding that
  is the prerequisite for fixing it: the fix would be to make the check and the insert
  one transaction, which needs the pending-row states thought through.

## Two claims on `main` that the code does not support

Recorded here because they cannot be corrected where they were made. The squash commit
[`b5fa9bd`](https://github.com/Nan0pk/Archiv/commit/b5fa9bd) and the body of
[#131](https://github.com/Nan0pk/Archiv/pull/131) both state things that are not true of
the code they describe. Rewriting history on `main` is worse than leaving an uncorrected
record, so the correction lives forward, here.

This repository exists because a record said work was finished that was not. Leaving that
same kind of statement standing in the record is the failure itself, not a wording
problem, which is why it is written down rather than let go.

**Claim one: that the parallel-add fix rebuilds derived artifacts.** The message says
"the old check treated the content as a duplicate and skipped rebuilding derived
artifacts. It is now treated as not yet ingested and the work is done."

It is not. Measured twice, by instrumenting the run: for a digest whose only earlier
ingestion failed, `derive_artifacts` is called **zero** times and `reuse_derived_artifacts`
once, and the evidence is recorded as `skipped` / `derived-existing`. That decision is
made in `prepare_candidate` in `src/archiv/ingestion/service.py`, which the change never
touched.

What the change does do for that case is real and worth having: the ingestion is recorded
as a new original rather than a duplicate, and archive and PDF attachment expansion now
run. Under the earlier code that ordering recorded zero originals *and* never expanded
the archive at all.

**Claim two: that the desktop gap was closed in the console.** The message says "The
desktop console now reads the origin out of the run's own JSON and says so in its status
line… The step allowed recording that as a known gap instead; surfacing it is better and
was not much more work."

The step pointed at `question_argv` in `src/archiv/ui/product.py`, whose only caller is
`src/archiv/ui/tk_product.py` — the window `archiv ui` opens with no flags. The stamp went
into `src/archiv/ui/tk_console.py`, which is the diagnostic view behind `--diagnostic`.
Half the named gap was closed; the half ordinary users see was not, and nothing was
recorded. The default window was corrected afterwards, so the code is now right in both
places — but the sentence on `main` was not true when it was written.

## The request field names for a paid model are unverified

`src/archiv/model_adapter.py` sends `max_tokens` to cap the reply, and
`src/archiv/cost_control.py` reads `usage.prompt_tokens` and `usage.completion_tokens`
back to record what was spent. Both follow the wire format the adapter already targeted.

Neither has been checked against a real provider, because no request has ever been sent
from this project's development environment. Newer OpenAI models expect
`max_completion_tokens` rather than `max_tokens`, and a request using the older name may
be rejected or the cap silently ignored — in which case the reply is unbounded and the
recorded spend understates it.

Check both on the first real call: that the cap is honoured, and that the usage block
arrives in the shape the recorder expects. Until then, the reply cap is an intention
rather than a measured behaviour.

## What a calibration run cannot measure here, and why

`archiv model calibrate` records what one grounded question costs. Three of the numbers
step S08 asks for are written as refusals to measure rather than as figures, and this is
not a gap to be closed by trying harder:

- **Time to first token.** Both adapters ask for a whole reply rather than a stream, so
  there is no first token to time.
- **Prompt-processing rate, and generation rate, separately.** Telling them apart needs
  the time to first token above. What can be measured without it — reply tokens divided
  by the whole wall clock, prompt processing included — is recorded instead, under a name
  that says so.
- **Answer quality.** It is copied from a field-trial run's scored results, or it is
  absent with a reason. It is never computed here. There is one definition in this
  project of whether an answer was good; a second one would eventually disagree with the
  first, and then two numbers would both claim to be the quality.

Streaming would make the first two measurable. It would also mean a second request shape
on the one path where document text leaves the machine, which is not a change to make in
passing.

Separately: **no calibration run has ever measured a paid model.** Everything exercised
so far used a stand-in that answers instantly. The timings are real timings of that
stand-in, which is to say they measure this machine and this archive, not a provider.
