#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=${1:-$(pwd)}
EVIDENCE_DIR=${2:-/tmp/archiv-offline-alpha-evidence}
HOME_DIR=${ARCHIV_HOME:-/tmp/archiv-offline-alpha-home}
RESTORE_DIR=${ARCHIV_RESTORE_HOME:-/tmp/archiv-offline-alpha-restored}
NETWORK_POLICY=${ARCHIV_NETWORK_POLICY:-not-enforced}

rm -rf "$HOME_DIR" "$RESTORE_DIR"
mkdir -p "$EVIDENCE_DIR"
find "$EVIDENCE_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

if command -v archiv >/dev/null 2>&1; then
  ARCHIV=(archiv)
elif [[ -f "$SOURCE_ROOT/src/archiv/cli.py" ]]; then
  export PYTHONPATH="$SOURCE_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
  ARCHIV=(python3 -m archiv.cli)
else
  echo "archiv command is not installed and no source tree was found" >&2
  exit 1
fi

started=$(date +%s)
"${ARCHIV[@]}" doctor --json | tee "$EVIDENCE_DIR/doctor.json"
"${ARCHIV[@]}" ingest "$SOURCE_ROOT/tests/fixtures/representative-corpus" --home "$HOME_DIR" \
  | tee "$EVIDENCE_DIR/ingest.json"
"${ARCHIV[@]}" search "unique fixture marker" --home "$HOME_DIR" \
  | tee "$EVIDENCE_DIR/search.json"
"${ARCHIV[@]}" run "$SOURCE_ROOT/tests/tasks/cross-file-report.yaml" --home "$HOME_DIR" \
  | tee "$EVIDENCE_DIR/run.json"
run_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_id"])' \
  "$EVIDENCE_DIR/run.json")
"${ARCHIV[@]}" verify "$run_id" --home "$HOME_DIR" | tee "$EVIDENCE_DIR/verify.json"
"${ARCHIV[@]}" backup "$EVIDENCE_DIR/archiv-backup.zip" --home "$HOME_DIR" --json \
  | tee "$EVIDENCE_DIR/backup.json"
"${ARCHIV[@]}" restore "$EVIDENCE_DIR/archiv-backup.zip" --home "$RESTORE_DIR" --json \
  | tee "$EVIDENCE_DIR/restore.json"
"${ARCHIV[@]}" search "unique fixture marker" --home "$RESTORE_DIR" \
  | tee "$EVIDENCE_DIR/restored-search.json"
"${ARCHIV[@]}" verify "$run_id" --home "$RESTORE_DIR" --no-render \
  | tee "$EVIDENCE_DIR/restored-verify.json"
finished=$(date +%s)

python3 - "$EVIDENCE_DIR" "$run_id" "$started" "$finished" "$NETWORK_POLICY" <<'PY'
import hashlib
import json
import platform
import sys
from pathlib import Path

root = Path(sys.argv[1])
run_id = sys.argv[2]
started = int(sys.argv[3])
finished = int(sys.argv[4])
network_policy = sys.argv[5]
files = []
for path in sorted(root.iterdir()):
    if not path.is_file() or path.name == "summary.json":
        continue
    files.append(
        {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
summary = {
    "schema_version": "1",
    "status": "succeeded",
    "network_policy": network_policy,
    "run_id": run_id,
    "duration_seconds": finished - started,
    "python": platform.python_version(),
    "platform": platform.platform(),
    "evidence": files,
}
(root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
PY
