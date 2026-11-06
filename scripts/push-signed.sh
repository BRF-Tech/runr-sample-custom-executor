#!/usr/bin/env bash
# Publishes a JSON document as the signed OCI artifact RunR reads: the runtime catalog of an
# oci-catalog source, or a source's patch feed.
#
#   scripts/push-signed.sh catalog catalog.json    ghcr.io/<you>/runr/sample-executor-catalog[:stable]
#   scripts/push-signed.sh feed    patch-feed.json ghcr.io/<you>/runr/sample-executor-patches[:feed]
#
#   1. oras push <repository>:<tag>-candidate: an OCI image manifest with the artifactType and a
#      single layer of the document's media type, empty config
#        catalog  application/vnd.runr.runtime-catalog.v1+json
#        feed     application/vnd.runr.patch-feed.v1+json
#   2. cosign sign --key by digest, no transparency log (--tlog-upload=false): the classic
#      sha256-<digest>.sig signature next to it
#   3. the signature verified with cosign.pub (the public key RunR is given), then <tag> moved to
#      the digest: the tag only ever names a signed document
#
# Environment: COSIGN_KEY - the private key, a cosign key reference (default
# env://COSIGN_PRIVATE_KEY: the PEM in that variable; or a file: COSIGN_KEY=cosign.key);
# COSIGN_PASSWORD - its password; COSIGN_PUBLIC_KEY - the public key (default cosign.pub).
# The registry login is Docker's (docker login ghcr.io; oras and cosign read it). PLAIN_HTTP=1
# talks plain HTTP to the registry: a local one for a dry run (docker run -p 5000:5000 registry:2).
# Needs oras (https://oras.land) and cosign (https://docs.sigstore.dev).
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

[ "$#" -eq 3 ] || die "usage: scripts/push-signed.sh catalog|feed FILE REPOSITORY[:TAG]"
kind="$1" file="$2" ref="$3"
case "$kind" in
  catalog) media_type="application/vnd.runr.runtime-catalog.v1+json" layer_name="catalog.json" default_tag="stable"
           description="RunR runtime catalog (schema 1)" ;;
  feed) media_type="application/vnd.runr.patch-feed.v1+json" layer_name="patch-feed.json" default_tag="feed"
        description="RunR patch feed (schema 1)" ;;
  *) die "unknown kind ${kind}: catalog or feed" ;;
esac
[ -f "$file" ] || die "no file ${file}"
command -v oras >/dev/null 2>&1 || die "oras is required (https://oras.land/docs/installation)"
command -v cosign >/dev/null 2>&1 || die "cosign is required (https://docs.sigstore.dev/cosign/system_config/installation/)"

# repository[:tag] — a tag is the part after the last ":" of the last path segment
repo="$ref" tag="$default_tag"
last="${ref##*/}"
if [[ "$last" == *:* ]]; then
  repo="${ref%:*}"
  tag="${last##*:}"
fi
[[ "$repo" == */* ]] || die "${ref}: not a repository with a registry host (ghcr.io/<you>/runr/...)"

public_key="${COSIGN_PUBLIC_KEY:-${ROOT}/cosign.pub}"
grep -q -- '-----BEGIN PUBLIC KEY-----' "$public_key" 2>/dev/null \
  || die "${public_key} is not a public key (still the placeholder?): generate the key pair first (keys/README.md)"
signing_key="${COSIGN_KEY:-env://COSIGN_PRIVATE_KEY}"
if [[ "$signing_key" == env://* ]]; then
  var="${signing_key#env://}"
  [ -n "${!var:-}" ] || die "the signing key is not set: ${var} (or COSIGN_KEY=<cosign key file>)"
fi

created="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
source_url="${IMAGE_SOURCE:-}"
if [ -z "$source_url" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  source_url="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}"
fi

oras_flags=() cosign_flags=()
if [ "${PLAIN_HTTP:-0}" = 1 ]; then
  oras_flags=(--plain-http)
  cosign_flags=(--allow-http-registry)
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/runr-push.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
cp -- "$file" "${work}/${layer_name}"

say "==> oras push ${repo}:${tag}-candidate (${media_type})"
annotations=(--annotation "org.opencontainers.image.created=${created}" --annotation "org.opencontainers.image.description=${description}")
[ -n "$source_url" ] && annotations+=(--annotation "org.opencontainers.image.source=${source_url}")
(cd "$work" && oras push ${oras_flags[@]+"${oras_flags[@]}"} "${repo}:${tag}-candidate" \
  --artifact-type "$media_type" "${annotations[@]}" "${layer_name}:${media_type}")

digest="$(oras manifest fetch ${oras_flags[@]+"${oras_flags[@]}"} --descriptor "${repo}:${tag}-candidate" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["digest"])')"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || die "cannot resolve ${repo}:${tag}-candidate"

say "==> cosign sign ${repo}@${digest}"
cosign sign ${cosign_flags[@]+"${cosign_flags[@]}"} --yes --tlog-upload=false --key "$signing_key" "${repo}@${digest}"

say "==> cosign verify with ${public_key#"${ROOT}"/}"
if ! cosign verify ${cosign_flags[@]+"${cosign_flags[@]}"} --key "$public_key" --insecure-ignore-tlog=true "${repo}@${digest}" >/dev/null; then
  die "the signature does not verify with ${public_key}: the signing key is not its pair; ${repo}:${tag} not moved"
fi

oras tag ${oras_flags[@]+"${oras_flags[@]}"} "${repo}@${digest}" "$tag"
ok "${repo}:${tag} → ${digest} (signed)"
