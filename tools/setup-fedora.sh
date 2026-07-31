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

if [[ ! -f /etc/fedora-release ]]; then
  echo "Archiv alpha setup supports Fedora only." >&2
  exit 1
fi

if [[ ${EUID} -eq 0 ]]; then
  DNF=(dnf)
else
  command -v sudo >/dev/null || { echo "sudo is required for package installation" >&2; exit 1; }
  DNF=(sudo dnf)
fi

"${DNF[@]}" install -y --setopt=install_weak_deps=False \
  python3 python3-pip libreoffice-writer poppler-utils

rm -rf "${PREFIX}/venv"
python3 -m venv "${PREFIX}/venv"
"${PREFIX}/venv/bin/python" -m pip install --upgrade pip
"${PREFIX}/venv/bin/python" -m pip install "${ROOT}"
mkdir -p "${PREFIX}/bin"
ln -sfn "${PREFIX}/venv/bin/archiv" "${PREFIX}/bin/archiv"
ln -sfn "${PREFIX}/venv/bin/archiv-mcp" "${PREFIX}/bin/archiv-mcp"

cat > "${PREFIX}/activate" <<EOF
export PATH="${PREFIX}/bin:\$PATH"
export ARCHIV_HOME="\${ARCHIV_HOME:-\$HOME/.local/share/archiv}"
EOF

"${PREFIX}/bin/archiv" doctor --json
cat <<EOF
Archiv offline alpha installed.

Add this line to your shell profile:
  source "${PREFIX}/activate"

Then run:
  archiv sample-vault ~/Archiv-Sample
  archiv ingest ~/Archiv-Sample
EOF
