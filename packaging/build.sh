#!/usr/bin/env bash
# Build a single-file desktop binary into dist/ (Linux). CI runs the same on Windows.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install --quiet "pyinstaller>=6"
SEP=":"; [[ "${OS:-}" == "Windows_NT" ]] && SEP=";"
pyinstaller --noconfirm --clean --onefile --name blockhost \
  --add-data "blockhost/ui${SEP}blockhost/ui" \
  --icon packaging/blockhost.ico --paths . \
  packaging/launcher.py
echo "Built: dist/blockhost$([[ "${OS:-}" == "Windows_NT" ]] && echo .exe)"
