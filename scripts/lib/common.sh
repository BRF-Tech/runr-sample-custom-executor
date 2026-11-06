# Shared by the scripts of this repository (sourced, no shebang).
# shellcheck shell=bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export ROOT

# shellcheck disable=SC2034 # BOLD and the others are used by the scripts that source this file
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  RED=$'\033[31m' GREEN=$'\033[32m' YELLOW=$'\033[33m' BOLD=$'\033[1m' NC=$'\033[0m'
else
  RED='' GREEN='' YELLOW='' BOLD='' NC=''
fi

say() { printf '%s\n' "$*"; }
ok() { printf '%s\n' "${GREEN}✓ $*${NC}"; }
warn() { printf '%s\n' "${YELLOW}! $*${NC}" >&2; }
fail() { printf '%s\n' "${RED}✗ $*${NC}" >&2; }
die() { fail "$*"; exit 1; }

# python3 with PyYAML (the scripts' manifest reader); PYTHON overrides the interpreter
PYTHON="${PYTHON:-python3}"
need_python() {
  command -v "$PYTHON" >/dev/null 2>&1 || die "python3 is required (PYTHON=<interpreter> to use another)"
  "$PYTHON" -c 'import yaml' 2>/dev/null || die "PyYAML is missing: pip install -r requirements.txt"
}

# lib — runs scripts/lib/runr_sample.py
lib() {
  "$PYTHON" "${ROOT}/scripts/lib/runr_sample.py" "$@"
}

# manifest_of ID — runtimes/ID/runtime.yaml, or die
manifest_of() {
  local file="${ROOT}/runtimes/$1/runtime.yaml"
  [ -f "$file" ] || die "no runtime $1 (no ${file#"${ROOT}"/})"
  printf '%s\n' "$file"
}
