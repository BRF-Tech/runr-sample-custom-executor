# RunR sample: custom executors

A repository of executors (runtimes) for [RunR](https://github.com/BRF-Tech/runr), ready to connect
to your RunR as a **runtime source**. It holds two languages that RunR does not ship:

| Runtime id | Language | Kind | Run mode | Versions |
|---|---|---|---|---|
| `sample-lua` | Lua | interpreted | `persistent`, through RunR's universal wrapper | `5.4` (image, default), `5.3` (dockerfile, deprecated) |
| `sample-zig` | Zig | compiled (`build` section) | `http`, the binary is its own server | `0.15` (image, default) |

Next to them: the scripts that check the manifests and run a contract test of every image locally,
GitHub Actions that test and publish the images, and an optional signed **runtime catalog** and
**patch feed** for RunR's `oci-catalog` sources and patch handling.

Fork it, replace the languages with yours, connect it. The reference for everything below is RunR's
[runtime documentation](https://github.com/BRF-Tech/runr/blob/main/docs/runtimes.md) and the
[executor development guide](https://github.com/BRF-Tech/runr/blob/main/docs/executor-development.md).

- [What is in the repository](#what-is-in-the-repository)
- [1. Fork or use this template](#1-fork-or-use-this-template)
- [2. Add a language](#2-add-a-language)
- [3. Test locally](#3-test-locally)
- [4. Publish](#4-publish)
- [5. Connect it to RunR](#5-connect-it-to-runr)
- [6. Lifecycle and patches](#6-lifecycle-and-patches)
- [7. Troubleshooting](#7-troubleshooting)
- [The schema copies](#the-schema-copies)

## What is in the repository

```text
runtimes/
  sample-lua/
    runtime.yaml        manifest: versions, template, dependencies, run mode
    Dockerfile          the runtime image (Lua, lua-cjson, LuaRocks, runner.lua)
    runner.lua          the persistent-mode worker: loads the function, answers request envelopes
  sample-zig/
    runtime.yaml        manifest with a build section
    Dockerfile          the toolchain image function builds run in
    build.zig           \
    runr.zig             } build.files: placed next to every function before its build
    runr_server.zig     /  (@file references in runtime.yaml)
tests/<id>/contract.env what the starter template answers (scripts/contract-test.sh)
tests/sample-lua/faas/  the dependency case of the contract test
scripts/
  validate.sh           offline manifest checks (schema + RunR's rules); --remote: runr runtimes validate
  contract-test.sh      builds an image and a function, starts it like RunR, calls each trigger
  build-catalog.sh      the catalog payload (or, with --feed, the patch feed)
  push-signed.sh        pushes a catalog or feed with oras and signs it with cosign
  lib/                  the shared Python and shell code of the scripts
schema/
  runtime.schema.json   RunR's manifest schema (a copy, see below)
  runtime-catalog.v1.json  the catalog payload
  patch-feed.v1.json    the patch feed (a copy from BRF-Tech/runr-runtimes)
patches.json            this source's patch feed (empty to start with)
cosign.pub              the public key of this repository's catalog and feed (a fork replaces it: keys/README.md)
.github/workflows/      ci.yml (pull requests), publish.yml (main, tags, weekly, by hand)
```

## 1. Fork or use this template

1. On GitHub: **Use this template → Create a new repository** (or fork). Public or private both
   work as a source.
2. **Your image namespace.** The manifests name images under BRF's namespace,
   `ghcr.io/brf-tech/runr/sample-executor-<lang>:<version>`. The publish workflow pushes with the
   repository's own `GITHUB_TOKEN`, which can write to `ghcr.io/<owner>/` only, so replace the
   prefix with yours:

   ```bash
   grep -rl 'ghcr.io/brf-tech/runr/' runtimes | xargs sed -i 's#ghcr.io/brf-tech/runr/#ghcr.io/<you>/runr/#g'
   ```

   (`<you>` in lower case; on macOS `sed -i ''`.) The workflow stops with a message naming the
   manifest if an image is outside `ghcr.io/<owner>/`.
3. **Your runtime ids.** Runtime ids are one namespace across a RunR platform: a second source with
   `sample-lua` would be refused on a RunR that already reads this sample. Rename each directory
   and its `id` together (`runtimes/acme-lua/runtime.yaml` with `id: acme-lua`, and
   `tests/acme-lua/`).
4. **Your contacts.** Change the address in [SECURITY.md](SECURITY.md) and the copyright line in
   [LICENSE](LICENSE).
5. Only for a signed catalog or patch feed: generate a key pair and replace
   [`cosign.pub`](cosign.pub), which holds this repository's own key ([keys/README.md](keys/README.md)).

## 2. Add a language

### Directory layout

A runtime is a directory `runtimes/<id>/` whose name is the runtime's `id`
(`^[a-z][a-z0-9-]{0,31}$`), with:

- `runtime.yaml`, the manifest ([reference](https://github.com/BRF-Tech/runr/blob/main/docs/runtimes.md#manifest-reference));
- the files of its runtime image: a `Dockerfile` and whatever it copies;
- files the manifest references with `@name` in `build.files`.

For the tests add `tests/<id>/contract.env` with the three answers of your starter template
(`EXPECT_HTTP`, `EXPECT_SCHEDULE`, `EXPECT_QUEUE`) and, if you want the dependency case to run your
own code, `tests/<id>/faas/` with the files that replace the template's.

Keep the runtime directory flat and small when it has a `dockerfile` version: every regular file
in it except `runtime.yaml` is the build context RunR uploads, at most 50 files and 900 KB together,
names of letters, digits, `.`, `-` and `_`. Subdirectories are not uploaded. Use a `git` version
for larger contexts.

### The manifest, field by field

[`runtimes/sample-lua/runtime.yaml`](runtimes/sample-lua/runtime.yaml) has a comment on every
section. What each part does:

| Section | In sample-lua | What RunR does with it |
|---|---|---|
| `apiVersion`, `kind`, `id`, `name`, `description`, `icon` | `sample-lua`, "Lua (sample)" | the runtime picker; `id` is stored on every project |
| `language`, `extensions` | `lua`, `[lua]` | the editor's highlighting; the handler file is `main.<first extension>` |
| `versions[]` | `5.4` (`image`), `5.3` (`dockerfile` + `buildArgs`) | one entry per selectable version; exactly one source each: `image`, `dockerfile`, `git` or `upload` |
| `versions[].eol`, `deprecated`, `sunset` | dates | [lifecycle](#6-lifecycle-and-patches) |
| `handler` | `file: main`, `function: handler`, `pattern` | `RUNR_FILENAME` / `RUNR_HANDLER`; the pattern is checked on every save of the entry file |
| `template.files` | `{file}`: the starter `main.lua` | the code a new project starts with; `{file}`, `{handler}` are filled in |
| `dependencies` | `function-dev-1.rockspec`, LuaRocks | a FaaS project with that file is built: install, copy, env |
| `build` | (sample-zig) | every saved version is compiled, see [below](#a-compiled-language-the-build-section) |
| `run` | `mode: persistent`, `command`, `workers: 2` | how invocations reach the code |
| `triggers`, `sandbox`, `resources` | all three triggers, all sandboxes, 250m / 128 MB | what projects of the runtime may use; defaults of new projects |

Write versions and dates as quoted strings (`"5.4"`, `"2027-06-30"`): unquoted, YAML reads a number
or a date and RunR refuses the manifest. `scripts/validate.sh` names every such value.

### The runtime image

Each version names where its image comes from:

| Source | RunR | This repository |
|---|---|---|
| `image: ghcr.io/<you>/runr/sample-executor-lua:5.4` | pulls the image (with the source's registry credentials when it is private) | `publish.yml` builds, tests and pushes it |
| `dockerfile: Dockerfile` + `buildArgs` | builds the image in each region with Kaniko from the runtime's directory, on first use | nothing to publish; the contract test builds it the same way |
| `git: {url, ref, path, dockerfile}` | builds it in each region from that repository | the contract test builds it from the URL |
| `upload: <name>` | runs an image uploaded to RunR | not testable here |

Conventions of this repository for `image` versions: CI builds `runtimes/<id>/Dockerfile.<version>`
when it exists, else `runtimes/<id>/Dockerfile`, always with `--build-arg RUNTIME_VERSION=<version>`,
and tags the result with the manifest's image name. Both Dockerfiles here take the language version
from `RUNTIME_VERSION`; the `5.3` dockerfile version passes the same argument through `buildArgs`.
Each image writes `/opt/runr/upstream` (`Lua 5.4.8`, `Zig 0.15.2`): the publish workflow reads the
full version from it for patch tags.

Pin base images by their full tag (`alpine:3.24.3`); a rebuild then changes only the packages the
base's branch updated. Runtime images run as root: RunR's function builds run their install and
build steps in the version's image and need to write `/build`.

### http mode or the universal wrapper

| Run mode | Who serves HTTP | Good for | Sample |
|---|---|---|---|
| `http` | your process, on `$PORT` | languages with a good HTTP server; compiled binaries | sample-zig |
| `exec` | the wrapper; your program runs **once per invocation** (request on stdin, response on stdout) | static binaries, shell scripts | RunR's C and Bash examples |
| `persistent` | the wrapper; `run.workers` long-lived processes, one JSON line in, one JSON line out | interpreters: no start-up per request | sample-lua |

The universal wrapper (`runr-wrapper`, image `ghcr.io/brf-tech/runr/wrapper:1.1.0`) turns HTTP
requests, queue CloudEvents and schedule ticks into the
[request envelope](https://github.com/BRF-Tech/runr/blob/main/docs/runtimes.md#request-envelope) and
your output into the HTTP response; it also answers CORS preflights and Knative probes and enforces
the timeout. A code-mounted function gets it from an init container at `/runr/runr-wrapper`; a
built function has it baked in at the same path. Your image does not contain it.

In `http` mode your server handles everything itself: plain requests, queue deliveries (a
CloudEvent POST in binary mode, `ce-type: dev.runr.queue.message`, a 2xx acknowledges) and, with
`run.schedule: invoke`, the schedule tick the wrapper POSTs once per cron run
(`ce-type: dev.runr.schedule.tick`). `run.command` is required for that: the wrapper starts it.

How the two samples implement the contract:

- **sample-lua** ([`runner.lua`](runtimes/sample-lua/runner.lua)): the wrapper starts
  `lua /opt/runr/runner.lua` per worker. The runner loads `RUNR_FILENAME` from `/app/code` once,
  then for every envelope line calls `handler(event, context)` and writes one JSON line: a string
  becomes the body, a table with `statusCode` the response envelope, any other table JSON.
  `print()` goes to the logs, so a debug line never breaks the protocol.
- **sample-zig** ([`runr_server.zig`](runtimes/sample-zig/runr_server.zig)): a small HTTP/1.1
  server on `$PORT` (Content-Length and chunked bodies, one thread per connection) that builds a
  `runr.Request` (trigger from the `ce-type` header), calls `handler(allocator, req)` and writes the
  `runr.Response`. The build command generates `runr_entry.zig`, which points the server at the
  project's entry file and handler (`{file}`, `{handler}`).

### Dependencies

A FaaS project that contains `dependencies.file` is built: `install` runs in `dependencies.image`
(default: the version's image) with the project's files in `/build/code`, each `copy` entry goes
from the build stage into the function's image, `env` is set there. `template` is the file a new
FaaS project starts with.

- **sample-lua**: `function-dev-1.rockspec` (LuaRocks wants `<package>-<version>.rockspec`);
  `luarocks --tree /build/vendor install --only-deps …`, `/build/vendor` copied to `/app/vendor`,
  `LUA_PATH` / `LUA_CPATH` pointing there (`{version}` is the Lua version). Pure-Lua rocks only:
  the image has no C compiler.
- **sample-zig**: `build.zig.zon`; `zig fetch --save <url>` adds a package, `zig build --fetch`
  downloads it before the build, [`build.zig`](runtimes/sample-zig/build.zig) imports every
  dependency under its name. The packages are compiled in, so there is nothing to `copy`.

### A compiled language: the build section

```yaml
build:
  image: "{image}"                 # the build stage: the version's (toolchain) image
  command: >-                      # runs in /build/code; everything in /build/out is the output
    echo 'pub const handler = @import("{file}").{handler};' > runr_entry.zig &&
    zig build -Doptimize=ReleaseSmall --prefix /build/out --summary none
  runImage: alpine:3.23.8          # the function's image
  source: false                    # do not copy the code into /app/code
  files:                           # placed next to the code before the build
    build.zig: "@build.zig"        # @name: the file next to the manifest, inlined by the source
```

For every saved version RunR generates a two-stage Dockerfile and runs it with Kaniko in the
project's region: `FROM <build.image> AS build`, the code plus `build.files` in `/build/code`, one
`RUN` with `mkdir -p /build/out`, the dependency install (when the project has the dependency file)
and `build.command`; then `FROM <build.runImage>`, `/build/out` copied to `/app`, the wrapper at
`/runr/runr-wrapper` when a trigger needs it, the `RUNR_*` environment, `EXPOSE 8080` and the
entrypoint (`run.command` in http mode, the wrapper otherwise). `scripts/contract-test.sh`
generates the same Dockerfile (`python3 scripts/lib/runr_sample.py dockerfile …` prints it).

A Docker Hub name without a registry host (`alpine:3.23.8`) goes through the region registry's
mirror, which caches it; a name with a host is pulled from there on every build.

`@file` references work only when RunR reads the manifest from a source (git or catalog); a
manifest registered by hand must carry the contents inline. `{http, schedule}` pairs pick a file
per trigger, like RunR's Go runtime does.

## 3. Test locally

You need Docker, curl and Python 3 with the pinned tools:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # check-jsonschema and PyYAML
```

### Validate the manifests

```bash
scripts/validate.sh                      # every runtimes/*/runtime.yaml and patches.json
```

It checks what RunR checks when it syncs a source: the JSON Schema (`schema/runtime.schema.json`),
then the rules a schema cannot express - the `id` matches the directory, `@file` references exist
next to the manifest, one source per version, exactly one default version, the `dockerfile` file is
in the directory, the files of a dockerfile runtime (names, 50 files, 900 KB), the fields of the run
mode, a sunset not before the deprecation, unquoted numbers and dates - and `patches.json` against
the patch feed schema. Every problem comes with its JSON path:

```text
runtimes/sample-lua/runtime.yaml: /versions/1/sunset: is a YAML date; quote it: "2027-06-30"
```

With a RunR at hand, let RunR itself judge each manifest (the [RunR CLI](https://github.com/BRF-Tech/runr/blob/main/docs/cli.md),
after `runr login`):

```bash
scripts/validate.sh --remote             # runr runtimes validate for every manifest
```

`--remote` sends each manifest with its `@file` references inlined, as RunR reads it from this
repository, and the runtime's directory as `--files` when it has a dockerfile version. By hand:

```bash
runr runtimes validate -f runtimes/sample-lua/runtime.yaml --files runtimes/sample-lua
python3 scripts/lib/runr_sample.py inline runtimes/sample-zig/runtime.yaml > /tmp/sample-zig.json
runr runtimes validate -f /tmp/sample-zig.json
```

(`runr runtimes validate -f runtimes/sample-zig/runtime.yaml` alone answers
`/build/files/build.zig: file references (@path) are only resolved in catalogs`: a registration
does not read files next to the manifest.)

### Run the contract test

```bash
scripts/contract-test.sh sample-lua          # every version
scripts/contract-test.sh sample-zig 0.15     # one version
scripts/contract-test.sh sample-lua 5.4 --pull   # the published image instead of a local build
```

Per version it:

1. builds the runtime image - for an image version tagged with the manifest's name, for a
   dockerfile version from exactly the files RunR would upload;
2. creates a function from the starter template: code-mounted at `/app/code` with the wrapper at
   `/runr/runr-wrapper` (sample-lua), or built with RunR's two-stage Dockerfile (sample-zig);
3. for a runtime with dependencies, does it again as a FaaS project with the dependency file
   (sample-lua installs a rock from luarocks.org; sample-zig fetches `build.zig.zon`);
4. starts one container per trigger with the environment RunR sets (`RUNR_MODE`, `RUNR_TRIGGER`,
   `RUNR_HANDLER`, `RUNR_FILENAME`, `RUNR_CODE_DIR`, `RUNR_RUNTIME`, `RUNR_RUNTIME_VERSION`,
   `RUNR_TIMEOUT`, `PORT`, the wrapper's `RUNR_WRAPPER_MODE`, `RUNR_COMMAND`, `RUNR_WORKERS`) and
   the command RunR gives it, then checks:

   | Trigger | Call | Passes when |
   |---|---|---|
   | http | `GET /hello?name=RunR` | status 200, the body contains `EXPECT_HTTP` |
   | queue | `POST /` with `ce-specversion: 1.0`, `ce-type: dev.runr.queue.message`, `ce-source`, `ce-id`, `ce-time`, `X-RunR-Delivery-Attempt`, body `{"msg":"hi"}` | a 2xx status, the body contains `EXPECT_QUEUE` |
   | schedule | the container with `RUNR_TRIGGER=schedule`: one invocation, then it exits | exit code 0, the output contains `EXPECT_SCHEDULE` |

```text
── sample-lua: 12 passed, 0 failed ──
  PASS  sample-lua 5.4 xaas http  (200: Hello, RunR! (from Lua))
  PASS  sample-lua 5.4 xaas schedule  (exit 0: tick from Lua )
  PASS  sample-lua 5.4 xaas queue  (200: processed hi (from Lua))
  …
```

A failure prints the function's last log lines. `KEEP=1` keeps the work directory (the generated
Dockerfiles, env files) and the containers; `--no-deps` skips the dependency case;
`RUNR_WRAPPER_IMAGE` tests another wrapper. The dependency case needs network access (LuaRocks).

[`runr dev`](https://github.com/BRF-Tech/runr/blob/main/docs/cli.md#runtime-plugins-in-runr-dev)
runs a project of a registered runtime locally in the same way, once the runtime is on your RunR.

### Lint

What CI runs on the scripts and workflows:

```bash
docker run --rm -v "$PWD":/mnt -w /mnt koalaman/shellcheck:v0.11.0 -x scripts/*.sh scripts/lib/*.sh
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:1.7.12 -color
```

## 4. Publish

### The images

[`publish.yml`](.github/workflows/publish.yml) builds every image version, contract-tests it and
pushes exactly the tested image under the manifest's name, with the repository's `GITHUB_TOKEN`
(`permissions: packages: write`):

- on every push to `main` and every `v*` tag;
- weekly, so the version tags pick up the fixes of the base image and its packages;
- by hand (**Actions → Publish → Run workflow**), for one runtime or version, optionally as a
  patch (see [Ship a patch](#ship-a-patch)).

Dockerfile versions are not published: RunR builds them. Pull requests run
[`ci.yml`](.github/workflows/ci.yml): lint, validation, the catalog build and the contract test of
every version, nothing pushed. Every action is pinned by its commit.

**Public or private packages.** A new package on ghcr.io is private. Either:

- make it public: the package → **Package settings** → **Change visibility** → Public. RunR then
  pulls without credentials; or
- keep it private and give the RunR source registry credentials (below): RunR uses them to read
  the source and creates image pull secrets for the function pods and for its builds. A token with
  `read:packages` is enough.

The images carry `org.opencontainers.image.source`, so GitHub links each package to the repository.

### Optional: the signed catalog and patch feed

With the repository secret `COSIGN_PRIVATE_KEY` (and `COSIGN_PASSWORD`) set
([keys/README.md](keys/README.md)), every publish run on `main` also pushes, after the images:

| Artifact | Reference | Content |
|---|---|---|
| runtime catalog | `ghcr.io/<owner>/runr/sample-executor-catalog:stable` (a `v*` tag: `:<tag>`) | every manifest as JSON (`@file` inlined), the build context of dockerfile runtimes, the patches of `patches.json` |
| patch feed | `ghcr.io/<owner>/runr/sample-executor-patches:feed` | `patches.json` (patch feed v1) |

Both are OCI artifacts pushed with `oras` (artifactType and the single layer's media type
`application/vnd.runr.runtime-catalog.v1+json` / `application/vnd.runr.patch-feed.v1+json`, empty
config) and signed with `cosign sign --key` (`sha256-<digest>.sig`, no transparency log). The tag
moves only after the signature verified with `cosign.pub`. Without the secret the job is skipped
and the repository works as a git source only.

Build and inspect them locally:

```bash
scripts/build-catalog.sh -o catalog.json        # checked against schema/runtime-catalog.v1.json
scripts/build-catalog.sh --feed -o patch-feed.json
```

Publish from your machine (logged in with `docker login ghcr.io`; oras and cosign installed):

```bash
COSIGN_KEY=cosign.key COSIGN_PASSWORD=… \
  scripts/push-signed.sh catalog catalog.json ghcr.io/<you>/runr/sample-executor-catalog:stable
```

`PLAIN_HTTP=1` lets the script talk to a local registry (`docker run -d -p 5000:5000 registry:2`)
for a dry run.

## 5. Connect it to RunR

**Who.** A platform administrator (`runtimes.manage`) connects a source for every organization; its
runtimes are platform runtimes. A member with `runtimes.use_custom` connects one for their own
organization while the platform allows custom runtimes (Administration → Regions & settings →
**Custom Runtimes**); its runtimes are that organization's custom runtimes, and their manifests may
not set `runtimeClass`.

### In the console

**Build & Run → Runtimes → Sources → Add source**:

| Field | Git source (this repository) | OCI catalog source |
|---|---|---|
| Kind | `git` | `oci-catalog` |
| Name | `my-executors` | `my-executors` |
| URL | `https://github.com/<you>/runr-sample-custom-executor` (or `git@github.com:<you>/runr-sample-custom-executor.git`) | `ghcr.io/<you>/runr/sample-executor-catalog:stable` |
| Ref | `main` (a branch or a tag) | - |
| Path | `runtimes` | - |
| Public key | only with a patch feed: the content of `cosign.pub` | the content of `cosign.pub` |
| Patch feed | `ghcr.io/<you>/runr/sample-executor-patches:feed` (optional) | not needed: the catalog carries the patches |
| Git credentials | private repository: an HTTPS token and username, or an SSH deploy key and known_hosts | - |
| Registry | private packages: registry host `ghcr.io`, username, password or token | the same, for a private catalog and private images |

**Test connection** reads the source without saving it and lists what it found and every problem.
**Sync now** reads it at once; afterwards RunR checks it on its interval (hourly by default) and
reads it again when the commit or the catalog digest changed. The source shows its status (`ok`,
`partial`, `unreachable`, `auth_failed`, `invalid_signature`, `invalid_catalog`), the revision it
read, whether the signature verified, and each problem with file and JSON path.

After a sync the runtimes appear in **Build & Run → Runtimes** and in the runtime picker of new
projects. RunR starts the region builds of `dockerfile` versions (Builds shows them per region) and
pulls `image` versions on first use.

### A public source

Nothing but the URL: the repository is cloned anonymously, public images are pulled anonymously.

### A private source

- **HTTPS token**: a GitHub fine-grained token with **Contents: read** on the repository (or a
  classic token with `repo`), the username `x-access-token` (the default) or your user. RunR passes
  it as an HTTP header, never on a command line.
- **SSH deploy key**: repository → **Settings → Deploy keys → Add deploy key** (read-only) with
  the public half of `ssh-keygen -t ed25519 -f deploy_key -N ''`; give RunR the private half and
  the host key (`ssh-keyscan github.com > known_hosts`). Without known_hosts the host key is
  accepted on first use.
- **Private images or catalog**: registry host `ghcr.io`, your username and a token with
  `read:packages`. RunR reads the catalog and patch feed with them and creates image pull secrets
  for the function pods and builds.

### With the CLI

```bash
runr runtimes sources add --name my-executors --kind git \
  --url https://github.com/<you>/runr-sample-custom-executor --ref main --path runtimes

# a private repository: an HTTPS token, or an SSH deploy key
GITHUB_TOKEN=… runr runtimes sources add --name my-executors --kind git \
  --url https://github.com/<you>/runr-sample-custom-executor --ref main --path runtimes \
  --git-token-env GITHUB_TOKEN
runr runtimes sources add --name my-executors --kind git \
  --url git@github.com:<you>/runr-sample-custom-executor.git --ref main --path runtimes \
  --ssh-key-file deploy_key --known-hosts-file known_hosts

# private images: registry credentials (with either of the above)
GHCR_TOKEN=… runr runtimes sources add … \
  --registry-host ghcr.io --registry-username <you> --registry-password-env GHCR_TOKEN

# the signed catalog
runr runtimes sources add --name my-executors --kind oci-catalog \
  --url ghcr.io/<you>/runr/sample-executor-catalog:stable --public-key-file cosign.pub

runr runtimes sources ls                 # every source with status and revision
runr runtimes sources sync <id>          # read it now
```

### With the API

```bash
API=https://runr.example.com/api
TOKEN=…   # an API token (Administration → API Tokens)

jq -n --rawfile key cosign.pub '{
  name: "my-executors", kind: "git",
  url: "https://github.com/<you>/runr-sample-custom-executor", ref: "main", path: "runtimes",
  public_key: $key, patch_feed: "ghcr.io/<you>/runr/sample-executor-patches:feed"
}' | curl -sS -X POST "$API/runtime-sources" \
  -H "Authorization: Bearer $TOKEN" -H 'Accept: application/json' \
  -H 'Content-Type: application/json' -d @-
```

The credentials are write-only fields of the same request: `git_token`, `git_username`,
`ssh_private_key`, `ssh_known_hosts`, `registry_host`, `registry_username`, `registry_password`.
`POST /api/runtime-sources/test` checks a source without saving it,
`POST /api/runtime-sources/{id}/sync` reads it now, `PUT /api/runtime-sources/{id}` changes it.

### With Terraform

The [RunR provider](https://registry.terraform.io/providers/brf-tech/runr/latest) has the resource
`runr_runtime_source`; its arguments are the API fields above:

```hcl
resource "runr_runtime_source" "executors" {
  name = "my-executors"
  kind = "git"
  url  = "https://github.com/<you>/runr-sample-custom-executor"
  ref  = "main"
  path = "runtimes"

  public_key = file("${path.module}/cosign.pub")
  patch_feed = "ghcr.io/<you>/runr/sample-executor-patches:feed"

  # a private repository and private images
  git_token         = var.github_token
  registry_host     = "ghcr.io"
  registry_username = "<you>"
  registry_password = var.ghcr_token
}
```

### Runtime ids are one namespace

Projects store the runtime id, so one id means one runtime on the whole platform. When a source
defines an id that RunR's built-in catalog, another source, a runtime registered by hand or another
organization already holds, the sync reports it and skips that runtime; the rest of the source is
read (status `partial`):

```text
runtimes/lua/runtime.yaml /id: The id 'lua' is already provided by the runtime source "other-executors"; this runtime is skipped.
```

That is why the samples are `sample-lua` and `sample-zig`, not `lua` and `zig`. Give yours a
distinctive prefix (`acme-lua`).

## 6. Lifecycle and patches

### End of life, deprecation, sunset

Per version (and, for the whole runtime, at the top level of the manifest):

| Field | Where | RunR |
|---|---|---|
| `eol: "YYYY-MM-DD"` | version | reminders 13, 4 and 1 weeks before (in-app; by mail a week ahead) to the projects' owners and the organization's owners and admins; after the date the version leaves the picker, its projects are flagged and can no longer be saved or deployed until they move to a newer version |
| `deprecated: "YYYY-MM-DD"` or `true` | version, runtime | from that date (`true`: now) a badge and a one-time notice; the version stays usable |
| `sunset: "YYYY-MM-DD"` | version, runtime | reminders 13, 4 and 1 weeks before; after the date it leaves the picker and its projects can no longer be saved or deployed (new projects of a sunset runtime are refused) |
| `replacement: <runtime id>` | runtime | the runtime RunR suggests in the deprecation and sunset notices |
| `notice: "…"` | runtime | up to 500 characters shown with the lifecycle state: why, and what to do |

Running deployments are never stopped by any of these. A sunset must not be before its deprecation.

The samples: every version has an `eol` (the end of support of its Alpine base); sample-lua `5.3`
is deprecated from 2026-12-01 with a sunset on 2027-06-30. The top-level fields are in both
manifests as comments, ready to uncomment when the runtime itself retires.

**Removing a version** from the manifest retires it: its projects run on the default version, as
with any retired version. Prefer a `sunset` first, so projects get their reminders.

**Removing a runtime** from the repository (deleting its directory): at the next sync RunR marks it
orphaned. It disappears from the picker, its projects keep running and deploying, and RunR sets a
sunset after a grace period (90 days by default), with the reminders above. A runtime that comes
back before is no longer orphaned.

### Ship a patch

A patch is a fixed image of an `image` version under an **immutable tag**, announced in the
source's signed patch feed (or catalog). RunR then shows every project on that version why the
patch came and lets its members apply it, pin one of the last five patches, or wait - until the
deadline of a critical or zero-day patch, when RunR moves the projects itself. RunR applies a
source's patches only to that source's own runtimes.

1. **Fix** the Dockerfile (or let a rebuild pick up a fixed base or package) and merge it.
2. **Publish the patch image**: **Actions → Publish → Run workflow** with the runtime
   (`sample-lua`), the version (`5.4`) and a revision (`1` for the first patch of that upstream
   release, then `2`, …). The run contract-tests the image, pushes the version tag and the
   immutable `<full version>-r<n>` tag (`5.4.8-r1`, from `/opt/runr/upstream`), refuses a tag that
   exists, and prints the `patches.json` entry with the image's digest in its summary.

   The digest of any tag, by hand:

   ```bash
   crane digest ghcr.io/<you>/runr/sample-executor-lua:5.4.8-r1
   docker buildx imagetools inspect ghcr.io/<you>/runr/sample-executor-lua:5.4.8-r1 --format '{{.Manifest.Digest}}'
   ```

3. **Add the entry** to `patches.json`, newest first, and fill in the level, the reason in English
   and Turkish, the CVEs and, for critical and zero-day patches, the deadline:

   ```json
   {
     "id": "sample-lua-5.4-5.4.8-r1",
     "runtime": "sample-lua",
     "version": "5.4",
     "tag": "5.4.8-r1",
     "image": "ghcr.io/<you>/runr/sample-executor-lua",
     "digest": "sha256:…",
     "level": "security",
     "reason": {"en": "lua-cjson rebuilt against the fixed musl", "tr": "lua-cjson düzeltilmiş musl ile yeniden derlendi"},
     "cves": ["CVE-2026-12345"],
     "url": "https://github.com/<you>/runr-sample-custom-executor/releases/tag/v1.2.0",
     "upstream": "Lua 5.4.8",
     "published_at": "2026-11-07T10:00:00Z"
   }
   ```

   `scripts/validate.sh` checks it: the schema, `id` = `<runtime>-<version>-<tag>`, the tag a patch
   of the version, the image the version's repository, a runtime and an image version of this
   repository, at most five patches per version.
4. **Commit to `main`**: the publish run signs and pushes the patch feed and the catalog. RunR reads
   them on the source's next check.

| Level | In RunR | Deadline |
|---|---|---|
| `routine` | a badge on the projects | none |
| `security` | a colored badge; in-app notices to the projects' owners and last deployers and the organization's owners and admins | none |
| `critical` | the same plus the platform administrators, in-app and by mail; a banner | `deadline_at`, else 14 days after publication |
| `zero-day` | everyone above whatever their notification settings, also on the organization's alert channels; a banner that stays | `deadline_at`, else 3 days after publication |

At a deadline RunR redeploys every project still behind the patch onto it (when the platform's
**Migrate at deadlines** policy is on) and rolls a project back, pinned, when the new revision does
not become ready. Details: [Runtime patches](https://github.com/BRF-Tech/runr/blob/main/docs/runtimes.md#runtime-patches).

## 7. Troubleshooting

**Sync problems.** Each problem names the file and the JSON path in the manifest; a runtime with a
problem is skipped and the others are read (status `partial`); a runtime that was valid before keeps
its last good manifest. Run `scripts/validate.sh` first: it finds most of them offline.

| Problem | Fix |
|---|---|
| `/versions/0/version: … is not of type 'string'` | quote it: `"5.4"` |
| `/versions/1/sunset: … is not of type 'string'` | quote the date: `"2027-06-30"` |
| `/id: The id 'x' does not match its directory 'y'.` | rename the directory or the id |
| `/build/files/<name>: references @x, which is not a file next to the manifest` | the file must be in the runtime's directory, not a link, no `..` |
| `/versions/1/dockerfile: names Dockerfile, which is not among the uploaded files` | the Dockerfile must be a regular file next to the manifest |
| `/: the files next to the manifest exceed 50 files or 900 KB` | move large build contexts to a `git` version |
| `/run/command: is required in persistent mode` | set `run.command` |
| `/sunset: is before the deprecation (…)` | fix the dates |
| `The repository has no directory runtimes` | the source's path; it is relative to the repository root |
| `The repository has no branch or tag main` | the source's ref |

**Signature failures** (`invalid_signature`). The catalog or feed is not signed with the private
key of the public key on the source: check it with
`cosign verify --key cosign.pub --insecure-ignore-tlog=true <reference>`. After a key rotation,
update the public key on the source. A tag pushed by hand without `scripts/push-signed.sh` has no
signature. RunR keeps the runtimes of the last good read meanwhile.

**Private registry or repository** (`auth_failed`; pods in `ImagePullBackOff`). The token lacks
`read:packages` (images, catalog) or repository read access (git), or the source's registry host
does not match the images' host (`ghcr.io`). A package created by the publish workflow is private
until you change its visibility.

**Id clashes.** `The id '…' is already provided by …` or `… is already taken on this platform`:
another runtime holds the id; rename yours (see [above](#runtime-ids-are-one-namespace)).
`… is defined twice in this source`: two directories with the same `id`.

**The wrapper is not found** (`exec /runr/runr-wrapper: no such file or directory`). RunR puts the
wrapper there for `exec` and `persistent` runtimes and for an http runtime's schedule trigger with
`run.schedule: invoke`; it is not in your image. Locally, `scripts/contract-test.sh` adds it. If
you start the image yourself, copy it in:
`COPY --from=ghcr.io/brf-tech/runr/wrapper:1.1.0 /runr-wrapper /runr/runr-wrapper`. An image with
its own `ENTRYPOINT` is fine: RunR replaces the command.

**The schedule tick fails.** `[runr-wrapper] schedule tick …: status 500` with your handler's
error before it: the handler failed (the tick counts as failed from status 400). In http mode the
server must listen on `0.0.0.0:$PORT` within 60 seconds and `run.command` must start it; a server
that ignores `$PORT` is never reached. The job's exit code is 1 then, and the CronJob shows it.

**The image is too large.** Every node pulls the image of the version on first use, so its size
is cold-start time. Keep the runtime image to the interpreter and its protocol code (the Lua image
is about 24 MB). A compiled language puts its toolchain only in the build stage: the Zig toolchain
image is about 750 MB, the function image is Alpine plus an 80 KB binary and the wrapper. A
function build has 15 minutes (the region's `BUILD_DEADLINE_SECONDS`); warming the toolchain's
cache in the image, as the Zig Dockerfile does, shortens every build.

## The schema copies

[`schema/runtime.schema.json`](schema/runtime.schema.json) is a copy of RunR's manifest schema
(`api/resources/runtimes/runtime.schema.json` of [BRF-Tech/runr](https://github.com/BRF-Tech/runr)),
including the lifecycle fields (`deprecated`, `sunset`, `replacement`, `notice`). It follows RunR:
refresh it when RunR's schema changes, and CI validates against the new rules:

```bash
curl -fsSL -o schema/runtime.schema.json \
  https://raw.githubusercontent.com/BRF-Tech/runr/main/api/resources/runtimes/runtime.schema.json
```

[`schema/patch-feed.v1.json`](schema/patch-feed.v1.json) is the patch feed schema of
[BRF-Tech/runr-runtimes](https://github.com/BRF-Tech/runr-runtimes) (`schema/patch-feed.v1.json`),
refreshed the same way.
[`schema/runtime-catalog.v1.json`](schema/runtime-catalog.v1.json) describes the catalog payload
this repository builds.

## License

MIT, see [LICENSE](LICENSE). Security reports: [SECURITY.md](SECURITY.md). Contributions:
[CONTRIBUTING.md](CONTRIBUTING.md).
