#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="Nan0pk/Archiv"
REF=${ARCHIV_REF:-main}
PREFIX=${ARCHIV_PREFIX:-"$HOME/.local/share/archiv-alpha"}
BIN_DIR=${ARCHIV_BIN_DIR:-"$HOME/.local/bin"}
SOURCE_DIR=""
SKIP_SYSTEM_PACKAGES=0

usage() {
  cat <<'EOF'
Install Archiv on Fedora without a repository checkout or shell activation.

Usage: install-fedora.sh [options]
  --ref REF                 Git ref to resolve and install (default: main)
  --prefix PATH             Versioned installation root
  --bin-dir PATH            Directory for archiv and archiv-mcp commands
  --source PATH             Install from a local source tree instead of GitHub
  --skip-system-packages    Do not run dnf (for prepared test environments)
  -h, --help                Show this help
EOF
}

while (($#)); do
  case "$1" in
    --ref)
      REF=${2:?missing value for --ref}
      shift 2
      ;;
    --prefix)
      PREFIX=${2:?missing value for --prefix}
      shift 2
      ;;
    --bin-dir)
      BIN_DIR=${2:?missing value for --bin-dir}
      shift 2
      ;;
    --source)
      SOURCE_DIR=${2:?missing value for --source}
      shift 2
      ;;
    --skip-system-packages)
      SKIP_SYSTEM_PACKAGES=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f /etc/fedora-release ]]; then
  echo "Archiv alpha installation currently supports Fedora only." >&2
  exit 1
fi

if ((SKIP_SYSTEM_PACKAGES == 0)); then
  if [[ ${EUID} -eq 0 ]]; then
    DNF=(dnf)
  else
    command -v sudo >/dev/null || {
      echo "sudo is required to install Fedora packages" >&2
      exit 1
    }
    DNF=(sudo dnf)
  fi
  "${DNF[@]}" install -y --setopt=install_weak_deps=False \
    python3 python3-pip libreoffice-writer poppler-utils curl tar gzip
fi

for command in python3 curl tar; do
  command -v "$command" >/dev/null || {
    echo "required command is missing: $command" >&2
    exit 1
  }
done

PREFIX=$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$PREFIX")
BIN_DIR=$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$BIN_DIR")
TEMP_DIR=""
SOURCE_COMMIT="local-source"
SOURCE_ARCHIVE_SHA256="not-applicable"

cleanup() {
  if [[ -n "$TEMP_DIR" ]]; then
    rm -rf "$TEMP_DIR"
  fi
}
trap cleanup EXIT

if [[ -n "$SOURCE_DIR" ]]; then
  SOURCE_DIR=$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$SOURCE_DIR")
  [[ -f "$SOURCE_DIR/pyproject.toml" ]] || {
    echo "local source does not contain pyproject.toml: $SOURCE_DIR" >&2
    exit 1
  }
  if command -v git >/dev/null && git -C "$SOURCE_DIR" rev-parse HEAD >/dev/null 2>&1; then
    SOURCE_COMMIT=$(git -C "$SOURCE_DIR" rev-parse HEAD)
  fi
else
  CURL_AUTH=()
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    CURL_AUTH=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
  elif [[ -n "${GH_TOKEN:-}" ]]; then
    CURL_AUTH=(-H "Authorization: Bearer ${GH_TOKEN}")
  elif command -v gh >/dev/null && gh auth token >/dev/null 2>&1; then
    CURL_AUTH=(-H "Authorization: Bearer $(gh auth token)")
  fi

  API_URL="https://api.github.com/repos/${REPOSITORY}/commits/${REF}"
  SOURCE_COMMIT=$(
    curl --fail --silent --show-error --location \
      --header "User-Agent: Archiv-Installer" \
      --header "Accept: application/vnd.github+json" \
      "${CURL_AUTH[@]}" \
      "$API_URL" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])'
  )
  if [[ ! "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
    echo "GitHub returned an invalid commit SHA for ref: $REF" >&2
    exit 1
  fi

  ARCHIVE="$TEMP_DIR/archiv-source.tar.gz"
  curl --fail --show-error --location --retry 3 \
    --header "User-Agent: Archiv-Installer" \
    "${CURL_AUTH[@]}" \
    "https://github.com/${REPOSITORY}/archive/${SOURCE_COMMIT}.tar.gz" \
    --output "$ARCHIVE"
  SOURCE_ARCHIVE_SHA256=$(sha256sum "$ARCHIVE" | awk '{print $1}')
  tar -tzf "$ARCHIVE" >/dev/null
  tar -xzf "$ARCHIVE" -C "$TEMP_DIR"
  SOURCE_DIR=$(find "$TEMP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'Archiv-*' -print -quit)
  [[ -n "$SOURCE_DIR" && -f "$SOURCE_DIR/pyproject.toml" ]] || {
    echo "downloaded source archive has an unexpected layout" >&2
    exit 1
  }
fi

VERSION=$(python3 - "$SOURCE_DIR/pyproject.toml" <<'PY'
import sys
import tomllib
from pathlib import Path

payload = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload["project"]["version"])
PY
)
[[ -n "$VERSION" ]] || {
  echo "could not determine Archiv version" >&2
  exit 1
}

VERSION_DIR="$PREFIX/versions/$VERSION"
rm -rf "$VERSION_DIR"
mkdir -p "$VERSION_DIR" "$BIN_DIR"
python3 -m venv "$VERSION_DIR"
"$VERSION_DIR/bin/python" -m pip install --upgrade pip
"$VERSION_DIR/bin/python" -m pip install "$SOURCE_DIR"
ln -sfn "$VERSION_DIR" "$PREFIX/current"
ln -sfn "$PREFIX/current/bin/archiv" "$BIN_DIR/archiv"
ln -sfn "$PREFIX/current/bin/archiv-mcp" "$BIN_DIR/archiv-mcp"

python3 - "$PREFIX/install.json" "$VERSION" "$SOURCE_COMMIT" \
  "$SOURCE_ARCHIVE_SHA256" "$BIN_DIR" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": "1",
    "version": sys.argv[2],
    "source_commit": sys.argv[3],
    "source_archive_sha256": sys.argv[4],
    "bin_dir": sys.argv[5],
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

"$BIN_DIR/archiv" doctor --json >/dev/null

echo "Archiv $VERSION installed."
echo "Source commit: $SOURCE_COMMIT"
echo "Command: $BIN_DIR/archiv"
echo "Data: ${ARCHIV_HOME:-$HOME/.local/share/archiv}"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Add $BIN_DIR to PATH, then open a new shell." ;;
esac
echo "Next: archiv add ~/Documents"
