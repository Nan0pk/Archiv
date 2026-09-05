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
