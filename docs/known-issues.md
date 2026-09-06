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
  reproducible fault inside one run — it failed roughly one run in four, and broke the
  required check on [#131](https://github.com/Nan0pk/Archiv/pull/131) — exchanged for a
  narrower one that needs two concurrent runs on the same archive.
- Consequences are a wrong "new originals" count and some duplicated expansion work. Not
  corruption: originals stay content-addressed, containment rows are written with
  `INSERT OR REPLACE`, and processing evidence is keyed on the digest.
- No remedy is planned yet, because it is not known whether concurrent adds against one
  archive is a scenario Archiv intends to support, and no test covers it. Deciding that
  is the prerequisite for fixing it: the fix would be to make the check and the insert
  one transaction, which needs the pending-row states thought through.
