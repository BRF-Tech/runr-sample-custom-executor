#!/usr/bin/env bash
# Builds the runtime catalog of this repository: the payload RunR reads from an oci-catalog
# runtime source (schema/runtime-catalog.v1.json), or the patch feed (schema/patch-feed.v1.json).
#
#   scripts/build-catalog.sh [-o catalog.json] [--repository URL] [--revision SHA] [--no-patches]
#   scripts/build-catalog.sh --feed [-o patch-feed.json]
#
# The catalog: every runtimes/<id>/runtime.yaml checked (scripts/validate.sh's rules), as JSON
# with the @file references of build.files inlined, the files of the runtimes with a dockerfile
# version (their build context), and the patches of patches.json; the result is checked against
# schema/runtime-catalog.v1.json. --repository and --revision default to the GitHub Actions
# run (GITHUB_SERVER_URL/GITHUB_REPOSITORY, GITHUB_SHA) or the checkout (git remote origin, HEAD).
#
# --feed: patches.json checked and its generated_at set to now - the patch feed to publish.
#
# Publish either with scripts/push-signed.sh. Needs python3 with PyYAML and jsonschema
# (pip install -r requirements.txt).
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

out="" repository="" revision="" patches=1 feed=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o|--out) out="${2:?-o needs a file}"; shift 2 ;;
    --repository) repository="${2:?--repository needs a URL}"; shift 2 ;;
    --revision) revision="${2:?--revision needs a commit}"; shift 2 ;;
    --no-patches) patches=0; shift ;;
    --feed) feed=1; shift ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
need_python

if [ "$feed" = 1 ]; then
  [ -f "${ROOT}/patches.json" ] || die "no patches.json"
  lib feed "${ROOT}/patches.json" ${out:+--out "$out"}
  [ -z "$out" ] || ok "${out}: patch feed ($(grep -c '"id":' "$out" || true) patches)"
  exit 0
fi

if [ -z "$repository" ]; then
  if [ -n "${GITHUB_REPOSITORY:-}" ]; then
    repository="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}"
  elif repository="$(git -C "$ROOT" remote get-url origin 2>/dev/null)"; then
    # git@github.com:you/repo.git → https://github.com/you/repo
    repository="${repository%.git}"
    if [[ "$repository" =~ ^[A-Za-z0-9._-]+@([^:]+):(.+)$ ]]; then
      repository="https://${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    fi
  else
    repository="local"
    warn "no git remote origin: source.repository is \"local\" (--repository URL sets it)"
  fi
fi
if [ -z "$revision" ]; then
  revision="${GITHUB_SHA:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)}"
  if [ -z "${GITHUB_SHA:-}" ] && [ -n "$(git -C "$ROOT" status --porcelain -- runtimes patches.json 2>/dev/null)" ]; then
    warn "runtimes/ or patches.json have uncommitted changes: the catalog is not exactly ${revision}"
  fi
fi

args=(catalog --root "$ROOT" --repository "$repository" --revision "$revision")
[ "$patches" = 1 ] && [ -f "${ROOT}/patches.json" ] && args+=(--patches "${ROOT}/patches.json")
[ -n "$out" ] && args+=(--out "$out")
lib "${args[@]}"

if [ -n "$out" ]; then
  if command -v check-jsonschema >/dev/null 2>&1; then
    check-jsonschema --schemafile "${ROOT}/schema/runtime-catalog.v1.json" "$out" >/dev/null
  fi
  ok "${out}: runtime catalog of ${repository} at ${revision}"
fi
