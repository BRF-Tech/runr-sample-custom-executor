#!/usr/bin/env bash
# Checks the runtime manifests the way RunR checks a runtime source, without a RunR:
#   1. the JSON Schema of RunR's manifests (schema/runtime.schema.json, check-jsonschema)
#   2. the rules a schema cannot express: id = directory name, @file references of build.files
#      exist next to the manifest, exactly one default version, one source per version, the files
#      of a dockerfile runtime (names, at most 50 files and 900 KB), the run mode's fields,
#      lifecycle dates in order, values YAML would turn into numbers or dates
#   3. patches.json: the patch feed schema (schema/patch-feed.v1.json) and its rules
#
#   scripts/validate.sh [runtimes/<id>/runtime.yaml ...]   (default: every runtime)
#   scripts/validate.sh --remote [...]                     also `runr runtimes validate` on each
#                                                          (the RunR CLI, logged in: runr login)
#
# Needs python3 with PyYAML and check-jsonschema: pip install -r requirements.txt
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

remote=0
manifests=()
for arg in "$@"; do
  case "$arg" in
    --remote) remote=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $arg (--remote)" ;;
    *) manifests+=("$arg") ;;
  esac
done
if [ "${#manifests[@]}" -eq 0 ]; then
  shopt -s nullglob
  manifests=("${ROOT}"/runtimes/*/runtime.yaml)
  shopt -u nullglob
  [ "${#manifests[@]}" -gt 0 ] || die "no runtimes/<id>/runtime.yaml"
fi

need_python
rc=0

say "${BOLD}── JSON Schema (schema/runtime.schema.json) ──${NC}"
schema_flag=()
if command -v check-jsonschema >/dev/null 2>&1; then
  check-jsonschema --schemafile "${ROOT}/schema/runtime.schema.json" "${manifests[@]}" || rc=1
else
  warn "check-jsonschema not found (pip install -r requirements.txt): the schema is checked with Python's jsonschema"
  schema_flag=(--schema "${ROOT}/schema/runtime.schema.json")
fi

say "${BOLD}── RunR's rules ──${NC}"
lib check ${schema_flag[@]+"${schema_flag[@]}"} "${manifests[@]}" || rc=1

if [ -f "${ROOT}/patches.json" ]; then
  say "${BOLD}── patches.json (patch feed v1) ──${NC}"
  lib patches "${ROOT}/patches.json" || rc=1
fi

if [ "$remote" = 1 ]; then
  say "${BOLD}── runr runtimes validate ──${NC}"
  command -v runr >/dev/null 2>&1 || die "the runr CLI is not installed (https://github.com/BRF-Tech/runr/blob/main/docs/cli.md)"
  work="$(mktemp -d "${TMPDIR:-/tmp}/runr-validate.XXXXXX")"
  trap 'rm -rf -- "$work"' EXIT
  for manifest in "${manifests[@]}"; do
    dir="$(dirname "$manifest")"
    id="$(basename "$dir")"
    # A registration takes no @file references (only sources resolve them): send the manifest
    # with them inlined, as RunR reads it from this repository. JSON is YAML.
    lib inline "$manifest" > "${work}/${id}.json" || { rc=1; continue; }
    files=()
    if grep -Eq '^[[:space:]]*dockerfile:' "$manifest"; then
      files=(--files "$dir")
    fi
    say "${manifest#"${ROOT}"/}"
    runr runtimes validate -f "${work}/${id}.json" ${files[@]+"${files[@]}"} || rc=1
  done
fi

if [ "$rc" = 0 ]; then ok "valid"; else fail "invalid (see above)"; fi
exit "$rc"
