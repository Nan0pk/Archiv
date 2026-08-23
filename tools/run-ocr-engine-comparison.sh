#!/usr/bin/env bash
set -euo pipefail

OUTPUT=${1:-"$HOME/archiv-ocr-benchmark"}
PRIVATE_CORPUS=${2:-}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENV="$OUTPUT/benchmark-venv"
EVIDENCE="$OUTPUT/evidence"
TESSDATA="$OUTPUT/tessdata"
URD_NAW_URL="https://raw.githubusercontent.com/tesseract-ocr/tessdata_contrib/1b7ada6f9ed0e165f06b3212500e1433fdf4dfc7/urd_naw/best/urd_naw.traineddata"
URD_NAW_BLOB="cb79560e7c97ea56082d1e285ffa3dcc319b1113"

if [[ ! -f /etc/fedora-release && -z ${ARCHIV_ALLOW_NON_FEDORA:-} ]]; then
  echo "This target command supports Fedora. Set ARCHIV_ALLOW_NON_FEDORA=1 only in a prepared CI image." >&2
  exit 1
fi

if [[ ${ARCHIV_SKIP_SYSTEM_PACKAGES:-0} != 1 ]]; then
  if [[ ${EUID} -eq 0 ]]; then
    DNF=(dnf)
  else
    command -v sudo >/dev/null || {
      echo "sudo is required to install benchmark packages" >&2
      exit 1
    }
    DNF=(sudo dnf)
  fi
  "${DNF[@]}" install -y --setopt=install_weak_deps=False \
    python3 python3-pip curl bubblewrap time libglvnd-glx \
    tesseract tesseract-langpack-eng tesseract-langpack-ara tesseract-langpack-urd \
    google-noto-sans-fonts google-noto-naskh-arabic-fonts \
    google-noto-nastaliq-urdu-fonts nafees-nastaleeq-fonts
fi

for command in python3 curl bwrap tesseract; do
  command -v "$command" >/dev/null || {
    echo "required command is missing: $command" >&2
    exit 1
  }
done

mkdir -p "$OUTPUT" "$TESSDATA"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -e "$ROOT[ocr-benchmark]"

SYSTEM_TESSDATA=$(tesseract --list-langs 2>&1 | sed -n 's/.*in "\([^"]*\)".*/\1/p' | head -n1)
[[ -d "$SYSTEM_TESSDATA" ]] || {
  echo "Tesseract did not expose its tessdata directory" >&2
  exit 1
}
for language in eng ara urd; do
  [[ -f "$SYSTEM_TESSDATA/$language.traineddata" ]] || {
    echo "missing $language.traineddata" >&2
    exit 1
  }
  ln -sfn "$SYSTEM_TESSDATA/$language.traineddata" "$TESSDATA/$language.traineddata"
done
curl --fail --show-error --location --retry 3 \
  "$URD_NAW_URL" \
  --output "$TESSDATA/urd_naw.traineddata"
"$VENV/bin/python" - "$TESSDATA/urd_naw.traineddata" "$URD_NAW_BLOB" <<'PY'
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = path.read_bytes()
actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit(f"urd_naw Git blob mismatch: expected {sys.argv[2]}, got {actual}")
print(f"urd_naw sha256={hashlib.sha256(data).hexdigest()} bytes={len(data)}")
PY

export TESSDATA_PREFIX="$TESSDATA"
export OMP_THREAD_LIMIT=${OMP_THREAD_LIMIT:-2}
ARGS=(
  benchmark-ocr
  --output "$EVIDENCE"
  --engines tesseract,rapidocr,kraken
  --candidates eng,ara,urd,eng+ara+urd,urd_naw,eng+ara+urd_naw
)
if [[ -n "$PRIVATE_CORPUS" ]]; then
  ARGS+=(--private-corpus "$PRIVATE_CORPUS")
fi

# Materialize optional models once. This result is not the final offline evidence.
"$VENV/bin/archiv" "${ARGS[@]}" >/dev/null

# Re-run the exact matrix with the network namespace removed. The final files are overwritten here.
bwrap \
  --unshare-net \
  --die-with-parent \
  --new-session \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --bind "$OUTPUT" "$OUTPUT" \
  --chdir "$ROOT" \
  --setenv TESSDATA_PREFIX "$TESSDATA" \
  --setenv OMP_THREAD_LIMIT "$OMP_THREAD_LIMIT" \
  --setenv ARCHIV_OCR_BENCHMARK_NETWORK denied-bubblewrap \
  -- "$VENV/bin/archiv" "${ARGS[@]}"

echo "Full report: $EVIDENCE/report.json"
echo "Human summary: $EVIDENCE/summary.md"
echo "Shareable aggregate: $EVIDENCE/shareable-summary.json"
