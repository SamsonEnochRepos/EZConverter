#!/usr/bin/env bash
# One-shot development setup: conda env "EZ" (Python 3.12, Node, conversion tools) + editable backend + JS workspace.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_NAME=EZ
PY_VERSION=3.12

echo "==> Conda environment ($ENV_NAME, Python $PY_VERSION)"
# Git Bash launched from PowerShell often lacks conda on PATH, so also try CONDA_EXE and the default install dirs.
if ! command -v conda >/dev/null; then
  for c in "${CONDA_EXE:-}" "$HOME/miniconda3/Scripts/conda.exe" "$HOME/anaconda3/Scripts/conda.exe" "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda"; do
    if [ -n "$c" ] && [ -x "$(cygpath -u "$c" 2>/dev/null || echo "$c")" ]; then PATH="$(dirname "$(cygpath -u "$c" 2>/dev/null || echo "$c")"):$PATH"; break; fi
  done
fi
command -v conda >/dev/null || { echo "conda is required (install Miniconda: https://docs.conda.io/en/latest/miniconda.html)"; exit 1; }
eval "$(conda shell.bash hook)"
if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "  env $ENV_NAME exists, reusing it"
else
  conda create -y -n "$ENV_NAME" -c conda-forge --override-channels "python=$PY_VERSION"
fi
conda activate "$ENV_NAME"
# Keep the env on the requested minor version even if it was created earlier with something else.
python -c "import sys; assert sys.version_info[:2] == tuple(map(int, '$PY_VERSION'.split('.'))), f'env $ENV_NAME runs Python {sys.version.split()[0]}, expected $PY_VERSION'"

echo "==> Conversion tools and Node (installed into the env)"
conda install -y -n "$ENV_NAME" -c conda-forge --override-channels ffmpeg pandoc poppler "nodejs>=22.12"

echo "==> Python packages"
python -m pip install -q --upgrade pip
python -m pip install -q -e "backend[dev,sevenzip]" --config-settings editable_mode=compat

echo "==> JavaScript workspace"
npm install -g --silent pnpm@10.28.0
pnpm install

echo "==> External conversion tools"
missing=0
for t in ffmpeg ffprobe soffice pandoc pdftoppm pdfinfo prlimit unshare; do
  if command -v "$t" >/dev/null; then printf "  ok       %s\n" "$t"; else printf "  MISSING  %s (the capabilities that need it will not be advertised)\n" "$t"; missing=1; fi
done
[ "$missing" = 0 ] || echo "soffice (LibreOffice) is not available from conda-forge; prlimit/unshare are Linux-only (util-linux). Install them system-wide if you need those capabilities."

echo "Done. Activate the environment with: conda activate $ENV_NAME"
echo "Run scripts/verify.sh (inside the activated env) to execute every check."
