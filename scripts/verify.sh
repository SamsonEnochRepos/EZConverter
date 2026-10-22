#!/usr/bin/env bash
# Runs every automated check that can run on this machine and reports what was and was not executed.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=python   # run inside the activated EZ conda env (conda activate EZ)
res=()
run() { local name="$1"; shift; echo; echo "=== $name"; if "$@"; then res+=("PASS  $name"); else res+=("FAIL  $name"); fi; }
skip() { res+=("SKIP  $1 ($2)"); }

run "python lint"            bash -c "cd backend && python -m ruff check . ../tests ../scripts"
run "python unit+integration+security" $PY -m pytest tests/unit tests/integration tests/security -q -n 4
run "python conformance (real engines)" bash -c "rm -rf tests/.conformance-results && $PY -m pytest tests/conformance -q -n 4 && $PY scripts/build_format_matrix.py"
run "contracts in sync"      node scripts/check-contracts.mjs
run "typescript typecheck"   pnpm -r typecheck
run "typescript tests"       pnpm -r test
run "web e2e (Chromium)"     pnpm --filter @ezconverter/web e2e
if command -v xvfb-run >/dev/null; then
  run "desktop e2e (Electron)" bash -c "pnpm --filter @ezconverter/web build && pnpm --filter @ezconverter/desktop build && cd apps/desktop && xvfb-run -a pnpm exec playwright test"
else skip "desktop e2e (Electron)" "xvfb-run not installed"; fi
run "mobile web-preview e2e" bash -c "pnpm --filter @ezconverter/mobile export:web && pnpm --filter @ezconverter/mobile exec playwright test"
run "mobile native bundles (Hermes)" bash -c "cd apps/mobile && CI=1 pnpm exec expo export --platform ios --platform android --output-dir dist-native"
run "pip-audit"              $PY -m pip_audit --skip-editable
skip "mobile on a physical device / emulator" "no device or emulator available"
skip "container image build" "requires a Docker daemon"
echo; echo "================ SUMMARY"; printf '%s\n' "${res[@]}"
printf '%s\n' "${res[@]}" | grep -q '^FAIL' && exit 1 || exit 0
