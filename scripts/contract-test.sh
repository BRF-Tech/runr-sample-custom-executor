#!/usr/bin/env bash
# Contract test of a runtime: builds its image and a function from its starter template, starts
# the function the way RunR does, and calls it once per trigger.
#
#   scripts/contract-test.sh <id> [version] [--pull] [--no-deps]
#
#   <id>        a runtime of this repository (runtimes/<id>/runtime.yaml)
#   [version]   one version; default: every version of the manifest
#   --pull      test the published image of an image version instead of building it
#   --no-deps   leave out the dependency build (the FaaS case below)
#
# What it does, per version (docs/runtimes.md of BRF-Tech/runr is the reference):
#   1. the runtime image: an image version is built from runtimes/<id>/Dockerfile.<version> or
#      Dockerfile with --build-arg RUNTIME_VERSION=<version> and tagged with the manifest's image
#      name (what the publish workflow pushes); a dockerfile version is built from the files RunR
#      uploads (the runtime's directory without runtime.yaml) with its buildArgs; a git version
#      from its repository
#   2. the function: the starter code of a new project (template.files) - mounted at /app/code
#      with the universal wrapper at /runr/runr-wrapper (what the ConfigMap and the init container
#      do), or, for a runtime with a build section, built with the two-stage Dockerfile RunR
#      generates (one image per trigger mode)
#   3. the FaaS case, for a runtime with dependencies: the template files plus the dependency
#      template (and tests/<id>/faas/, when it exists) built like RunR's dependency build
#   4. per trigger, a container with the environment RunR gives it (RUNR_*, PORT, the wrapper's
#      variables) and its command, then:
#        http      GET /hello?name=RunR             status 200, body contains EXPECT_HTTP
#        queue     POST of a CloudEvent (binary)    status 2xx, body contains EXPECT_QUEUE
#        schedule  RUNR_TRIGGER=schedule            exit 0, output contains EXPECT_SCHEDULE
#      The EXPECT_* texts are in tests/<id>/contract.env.
#
# Environment: RUNR_WRAPPER_IMAGE (default ghcr.io/brf-tech/runr/wrapper:1.1.0), IMAGE_SOURCE and
# IMAGE_REVISION (labels of the runtime image), KEEP=1 keeps the work directory and the containers.
# Needs docker, curl and python3 with PyYAML (pip install -r requirements.txt).
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

WRAPPER_IMAGE="${RUNR_WRAPPER_IMAGE:-ghcr.io/brf-tech/runr/wrapper:1.1.0}"
QUEUE_BODY='{"msg":"hi"}'
START_TIMEOUT=60

id="" only_version="" pull=0 deps=1
for arg in "$@"; do
  case "$arg" in
    --pull) pull=1 ;;
    --no-deps) deps=0 ;;
    -h|--help) sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $arg" ;;
    *) if [ -z "$id" ]; then id="$arg"; elif [ -z "$only_version" ]; then only_version="$arg"; else die "too many arguments"; fi ;;
  esac
done
[ -n "$id" ] || die "usage: scripts/contract-test.sh <id> [version] [--pull] [--no-deps]"
command -v docker >/dev/null 2>&1 || die "docker is required"
command -v curl >/dev/null 2>&1 || die "curl is required"
need_python
manifest="$(manifest_of "$id")"
runtime_dir="$(dirname "$manifest")"

expect_file="${ROOT}/tests/${id}/contract.env"
[ -f "$expect_file" ] || die "no ${expect_file#"${ROOT}"/}: it names the answers the starter template gives (EXPECT_HTTP, EXPECT_SCHEDULE, EXPECT_QUEUE)"
EXPECT_HTTP="" EXPECT_SCHEDULE="" EXPECT_QUEUE=""
# shellcheck source=/dev/null
source "$expect_file"

work="$(mktemp -d "${TMPDIR:-/tmp}/runr-contract.XXXXXX")"
containers=()
cleanup() {
  if [ "${KEEP:-0}" = 1 ]; then
    say "kept: ${work} and the containers ${containers[*]:-}"
    return
  fi
  if [ "${#containers[@]}" -gt 0 ]; then
    docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$work"
}
trap cleanup EXIT

results=()
failures=0
record() { # record STATUS WHAT [DETAIL]
  results+=("$1  $2${3:+  ($3)}")
  if [ "$1" = PASS ]; then ok "$2"; else fail "$2${3:+: $3}"; failures=$((failures + 1)); fi
}

image_labels=()
[ -n "${IMAGE_SOURCE:-}" ] && image_labels+=(--label "org.opencontainers.image.source=${IMAGE_SOURCE}")
[ -n "${IMAGE_REVISION:-}" ] && image_labels+=(--label "org.opencontainers.image.revision=${IMAGE_REVISION}")

# runtime_image VERSION — builds (or pulls) the version's runtime image; RUNTIME_IMAGE its name
runtime_image() {
  local version="$1" dockerfile ctx
  case "$SOURCE_KIND" in
    image)
      RUNTIME_IMAGE="$SOURCE_IMAGE"
      if [ "$pull" = 1 ]; then
        say "pull ${RUNTIME_IMAGE}"
        docker pull -q "$RUNTIME_IMAGE" >/dev/null
        return
      fi
      dockerfile="${runtime_dir}/Dockerfile.${version}"
      [ -f "$dockerfile" ] || dockerfile="${runtime_dir}/Dockerfile"
      [ -f "$dockerfile" ] || die "${id} ${version}: no ${dockerfile#"${ROOT}"/} to build ${RUNTIME_IMAGE} from"
      say "build ${RUNTIME_IMAGE} (${dockerfile#"${ROOT}"/}, RUNTIME_VERSION=${version})"
      docker build -q -t "$RUNTIME_IMAGE" -f "$dockerfile" --build-arg "RUNTIME_VERSION=${version}" \
        ${image_labels[@]+"${image_labels[@]}"} "$runtime_dir" >/dev/null
      ;;
    dockerfile)
      # the build context RunR uploads: every regular file next to the manifest but runtime.yaml
      RUNTIME_IMAGE="runr-contract/${id}:${version}"
      ctx="${work}/runtime-${version}"
      mkdir -p "$ctx"
      find "$runtime_dir" -maxdepth 1 -type f ! -name runtime.yaml -exec cp -- {} "$ctx/" \;
      say "build ${RUNTIME_IMAGE} (dockerfile version: ${SOURCE_DOCKERFILE} ${BUILD_ARGS[*]:-})"
      docker build -q -t "$RUNTIME_IMAGE" -f "${ctx}/${SOURCE_DOCKERFILE}" ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} "$ctx" >/dev/null
      ;;
    git)
      RUNTIME_IMAGE="runr-contract/${id}:${version}"
      say "build ${RUNTIME_IMAGE} (git version: ${SOURCE_GIT_URL}#${SOURCE_GIT_REF}:${SOURCE_GIT_PATH})"
      docker build -q -t "$RUNTIME_IMAGE" -f "$SOURCE_DOCKERFILE" ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} \
        "${SOURCE_GIT_URL}#${SOURCE_GIT_REF}:${SOURCE_GIT_PATH}" >/dev/null
      ;;
    upload)
      RUNTIME_IMAGE=""
      ;;
  esac
}

# function_image VERSION VARIANT TRIGGER — the function's image for a trigger; FUNCTION_IMAGE, BUILT
function_image() {
  local version="$1" variant="$2" trigger="$3" mode ctx code
  code="${work}/code-${version}-${variant}"
  mode=http
  [ "$trigger" = schedule ] && mode=schedule
  if [ "$HAS_BUILD" = 1 ] || [ "$variant" = faas ]; then
    # a function build: RunR's generated Dockerfile (FunctionBuildService)
    BUILT=1
    FUNCTION_IMAGE="runr-contract/${id}-fn:${version}-${variant}-${mode}"
    ctx="${work}/build-${version}-${variant}-${mode}"
    if [ ! -d "$ctx" ]; then
      mkdir -p "$ctx"
      cp -R -- "$code" "${ctx}/code"
      lib dockerfile "$manifest" "$version" "$RUNTIME_IMAGE" "$trigger" "${ctx}/code" "$WRAPPER_IMAGE" > "${ctx}/Dockerfile"
      say "build ${FUNCTION_IMAGE} (function build, ${variant}, mode ${mode})"
      if ! docker build -q -t "$FUNCTION_IMAGE" "$ctx" > "${ctx}/build.log" 2>&1; then
        cat "${ctx}/Dockerfile" >&2
        cat "${ctx}/build.log" >&2
        return 1
      fi
    fi
  else
    # code mounted at /app/code, the wrapper copied to /runr by an init container
    BUILT=0
    FUNCTION_IMAGE="runr-contract/${id}-fn:${version}-${variant}"
    ctx="${work}/mount-${version}-${variant}"
    if [ ! -d "$ctx" ]; then
      mkdir -p "$ctx"
      cp -R -- "$code" "${ctx}/code"
      {
        echo "FROM ${RUNTIME_IMAGE}"
        echo "COPY code/ /app/code/"
        if [ "$USES_WRAPPER" = 1 ]; then
          echo "COPY --from=${WRAPPER_IMAGE} /runr-wrapper /runr/runr-wrapper"
        fi
      } > "${ctx}/Dockerfile"
      say "build ${FUNCTION_IMAGE} (code mounted at /app/code)"
      if ! docker build -q -t "$FUNCTION_IMAGE" "$ctx" > "${ctx}/build.log" 2>&1; then
        cat "${ctx}/build.log" >&2
        return 1
      fi
    fi
  fi
}

# run_args VERSION TRIGGER NAME — RUN_ARGS: docker run arguments of the function container
run_args() {
  local envfile="${work}/env-$1-$2"
  local built_flag=()
  [ "$BUILT" = 1 ] && built_flag=(--built)
  eval "$(lib container "$manifest" "$1" "$2" "$envfile" ${built_flag[@]+"${built_flag[@]}"})"
  RUN_ARGS=(--name "$3" --env-file "$envfile")
  if [ "${#CONTAINER_COMMAND[@]}" -gt 0 ]; then
    RUN_ARGS+=(--entrypoint "${CONTAINER_COMMAND[0]}" "$FUNCTION_IMAGE" "${CONTAINER_COMMAND[@]:1}")
  else
    RUN_ARGS+=("$FUNCTION_IMAGE")
  fi
}

# serve NAME — starts the container of RUN_ARGS detached with port 8080 published; PORT_URL
serve() {
  local name="$1" port i
  # the image goes last in RUN_ARGS: -p must come before it
  docker run -d -p 127.0.0.1::8080 "${RUN_ARGS[@]}" >/dev/null
  containers+=("$name")
  port="$(docker port "$name" 8080/tcp | head -n 1)"
  port="${port##*:}"
  PORT_URL="http://127.0.0.1:${port}"
  for ((i = 0; i < START_TIMEOUT * 4; i++)); do
    if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != true ]; then
      return 1
    fi
    if curl -s -o /dev/null -m 2 -H 'K-Kubelet-Probe: contract-test' "${PORT_URL}/"; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

logs() { docker logs "$1" 2>&1 | tail -n 40 | sed 's/^/    | /' >&2; }

check_http() {
  local name="$1" what="$2" status body="${work}/$1.body"
  if ! serve "$name"; then record FAIL "$what" "the server did not start within ${START_TIMEOUT}s"; logs "$name"; return; fi
  status="$(curl -s -m 30 -o "$body" -w '%{http_code}' "${PORT_URL}/hello?name=RunR")" || status=000
  if [ "$status" = 200 ] && grep -qF -- "$EXPECT_HTTP" "$body"; then
    record PASS "$what" "200: $(head -c 120 "$body")"
  else
    record FAIL "$what" "status ${status}, body '$(head -c 200 "$body")', expected 200 with '${EXPECT_HTTP}'"
    logs "$name"
  fi
}

check_queue() {
  local name="$1" what="$2" status body="${work}/$1.body"
  if ! serve "$name"; then record FAIL "$what" "the server did not start within ${START_TIMEOUT}s"; logs "$name"; return; fi
  # what the queue consumer posts for a stream entry (docs/runtimes.md, http mode, queue trigger)
  status="$(curl -s -m 30 -o "$body" -w '%{http_code}' -X POST \
    -H 'ce-specversion: 1.0' -H 'ce-type: dev.runr.queue.message' \
    -H 'ce-source: /redisstream/contract/contract-test' -H 'ce-id: 1730000000000-0' \
    -H "ce-time: $(date -u +%Y-%m-%dT%H:%M:%SZ)" -H 'X-RunR-Delivery-Attempt: 1' \
    -H 'Content-Type: application/json' --data-binary "$QUEUE_BODY" "${PORT_URL}/")" || status=000
  if [[ "$status" == 2?? ]] && grep -qF -- "$EXPECT_QUEUE" "$body"; then
    record PASS "$what" "${status}: $(head -c 120 "$body")"
  else
    record FAIL "$what" "status ${status}, body '$(head -c 200 "$body")', expected 2xx with '${EXPECT_QUEUE}'"
    logs "$name"
  fi
}

check_schedule() {
  local name="$1" what="$2" rc=0 out="${work}/$1.out" err="${work}/$1.err"
  containers+=("$name")
  # the CronJob's container: invoke once, exit 0 for a status below 400 (timeout: a stuck tick)
  if command -v timeout >/dev/null 2>&1; then
    timeout 120 docker run "${RUN_ARGS[@]}" > "$out" 2> "$err" || rc=$?
  else
    docker run "${RUN_ARGS[@]}" > "$out" 2> "$err" || rc=$?
  fi
  if [ "$rc" = 0 ] && grep -qF -- "$EXPECT_SCHEDULE" "$out"; then
    record PASS "$what" "exit 0: $(head -c 120 "$out" | tr '\n' ' ')"
  else
    record FAIL "$what" "exit ${rc}, output '$(head -c 200 "$out")', expected exit 0 with '${EXPECT_SCHEDULE}'"
    sed 's/^/    | /' "$err" | tail -n 40 >&2
  fi
}

versions=()
while read -r version _; do versions+=("$version"); done < <(lib versions "$manifest")
if [ -n "$only_version" ]; then
  printf '%s\n' "${versions[@]}" | grep -qxF -- "$only_version" || die "${id} has no version ${only_version} (${versions[*]})"
  versions=("$only_version")
fi

for version in "${versions[@]}"; do
  say "${BOLD}── ${id} ${version} ──${NC}"
  eval "$(lib plan "$manifest" "$version")"
  if [ "$SOURCE_KIND" = upload ]; then
    warn "${id} ${version}: an upload version (${SOURCE_UPLOAD}) is an image uploaded to RunR: test that image with an image version locally"
    continue
  fi
  [ -n "$VERSION_SUNSET" ] && warn "${id} ${version}: sunset ${VERSION_SUNSET} (deprecated ${VERSION_DEPRECATED:-no})"
  if ! runtime_image "$version"; then record FAIL "${id} ${version}: runtime image"; continue; fi

  variants=(xaas)
  if [ "$HAS_DEPS" = 1 ] && [ "$deps" = 1 ]; then variants+=(faas); fi
  for variant in "${variants[@]}"; do
    code="${work}/code-${version}-${variant}"
    if [ "$variant" = faas ]; then
      lib code "$manifest" "$code" --faas
      if [ -d "${ROOT}/tests/${id}/faas" ]; then cp -R -- "${ROOT}/tests/${id}/faas/." "$code/"; fi
    else
      lib code "$manifest" "$code"
    fi
    for trigger in "${TRIGGERS[@]}"; do
      what="${id} ${version} ${variant} ${trigger}"
      if ! function_image "$version" "$variant" "$trigger"; then record FAIL "$what" "the function build failed"; continue; fi
      name="runr-contract-${id}-${version//./-}-${variant}-${trigger}-$$"
      run_args "$version" "$trigger" "$name"
      case "$trigger" in
        http) check_http "$name" "$what" ;;
        queue) check_queue "$name" "$what" ;;
        schedule) check_schedule "$name" "$what" ;;
      esac
    done
  done
done

say "${BOLD}── ${id}: $((${#results[@]} - failures)) passed, ${failures} failed ──${NC}"
if [ "${#results[@]}" -gt 0 ]; then printf '  %s\n' "${results[@]}"; fi
[ "$failures" = 0 ]
