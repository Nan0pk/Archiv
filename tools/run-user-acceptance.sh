#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=${1:-$(pwd)}
EVIDENCE_DIR=${2:-/tmp/archiv-user-acceptance}
HOME_DIR=${ARCHIV_USER_HOME:-/tmp/archiv-user-home}
RESTORE_DIR=${ARCHIV_USER_RESTORE_HOME:-/tmp/archiv-user-restored}
VAULT_DIR=${ARCHIV_USER_VAULT:-/tmp/archiv-user-vault}

rm -rf "$HOME_DIR" "$RESTORE_DIR" "$VAULT_DIR" "$EVIDENCE_DIR"
mkdir -p "$EVIDENCE_DIR"

if command -v archiv >/dev/null 2>&1; then
  ARCHIV=(archiv)
elif [[ -f "$SOURCE_ROOT/src/archiv/cli.py" ]]; then
  export PYTHONPATH="$SOURCE_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
  ARCHIV=(python3 -m archiv.cli)
else
  echo "archiv command is not installed and no source tree was found" >&2
  exit 1
fi

"${ARCHIV[@]}" sample-vault "$VAULT_DIR" | tee "$EVIDENCE_DIR/sample-vault.txt"
"${ARCHIV[@]}" add "$VAULT_DIR" --home "$HOME_DIR" | tee "$EVIDENCE_DIR/add.txt"
"${ARCHIV[@]}" status --home "$HOME_DIR" | tee "$EVIDENCE_DIR/status.txt"
"${ARCHIV[@]}" find "unique fixture marker" --home "$HOME_DIR" \
  | tee "$EVIDENCE_DIR/find.txt"
"${ARCHIV[@]}" report "unique fixture marker" --deterministic --home "$HOME_DIR" \
  --title "User-ready alpha evidence report" \
  | tee "$EVIDENCE_DIR/report.txt"

REPORT_PATH=$(sed -n 's/^Report: //p' "$EVIDENCE_DIR/report.txt")
[[ -n "$REPORT_PATH" && -f "$REPORT_PATH" ]] || {
  echo "human report output did not name a valid DOCX" >&2
  exit 1
}
cp "$REPORT_PATH" "$EVIDENCE_DIR/user-report.docx"

BACKUP_PATH="$EVIDENCE_DIR/archiv-backup.zip"
"${ARCHIV[@]}" backup "$BACKUP_PATH" --home "$HOME_DIR" \
  | tee "$EVIDENCE_DIR/backup.txt"
"${ARCHIV[@]}" restore "$BACKUP_PATH" --home "$RESTORE_DIR" \
  | tee "$EVIDENCE_DIR/restore.txt"
"${ARCHIV[@]}" find "unique fixture marker" --home "$RESTORE_DIR" \
  | tee "$EVIDENCE_DIR/restored-find.txt"
"${ARCHIV[@]}" status --home "$RESTORE_DIR" \
  | tee "$EVIDENCE_DIR/restored-status.txt"

grep -F "Added: 3 file(s)" "$EVIDENCE_DIR/add.txt" >/dev/null
grep -F "Found 3 verified match(es)" "$EVIDENCE_DIR/find.txt" >/dev/null
grep -F "Verified: yes" "$EVIDENCE_DIR/report.txt" >/dev/null
grep -F "Search index rebuilt: yes" "$EVIDENCE_DIR/restore.txt" >/dev/null
grep -F "Found 3 verified match(es)" "$EVIDENCE_DIR/restored-find.txt" >/dev/null

python3 - "$EVIDENCE_DIR" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
files = []
for path in sorted(item for item in root.iterdir() if item.is_file()):
    if path.name == "summary.json":
        continue
    files.append(
        {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
(root / "summary.json").write_text(
    json.dumps(
        {
            "schema_version": "1",
            "status": "succeeded",
            "interface": "human-readable-defaults",
            "evidence": files,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
