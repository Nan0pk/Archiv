#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PREFIX="${HOME}/.local/share/archiv-alpha"

while (($#)); do
  case "$1" in
    --prefix)
      PREFIX=${2:?missing value for --prefix}
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

exec /bin/bash "$ROOT/tools/install-fedora.sh" \
  --source "$ROOT" \
  --prefix "$PREFIX" \
  --bin-dir "$PREFIX/bin"
