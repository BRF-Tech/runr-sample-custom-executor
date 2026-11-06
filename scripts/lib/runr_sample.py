#!/usr/bin/env python3
"""Helpers of this repository's scripts: they read the runtime manifests the way RunR reads a
runtime source, check them, plan the local contract test and build the catalog payload.

    runr_sample.py check [--schema FILE] MANIFEST...      problems of the manifests (exit 1)
    runr_sample.py inline MANIFEST                        the manifest as JSON, @file references inlined
    runr_sample.py versions MANIFEST                      "<version> <source> <default>" per version
    runr_sample.py plan MANIFEST VERSION                  shell assignments for contract-test.sh
    runr_sample.py code MANIFEST DIR [--faas]             the starter files of a new project
    runr_sample.py dockerfile MANIFEST VERSION IMAGE TRIGGER CODEDIR WRAPPER_IMAGE
                                                          the function build's Dockerfile
    runr_sample.py container MANIFEST VERSION TRIGGER ENVFILE [--built]
                                                          the function container: environment, command
    runr_sample.py catalog --root DIR --repository URL --revision REV [--patches FILE]
                                                          the runtime catalog payload (schema 1)
    runr_sample.py patches FILE --root DIR                checks a patch feed against the runtimes
    runr_sample.py feed FILE [--out OUT]                  the patch feed to publish (generated_at now)
    runr_sample.py matrix [--images]                      the CI matrix of runtime versions (JSON)

Needs PyYAML; the JSON Schema checks need the jsonschema package (pip install -r requirements.txt).
RunR's own code is the reference for every rule here (BRF-Tech/runr: ManifestValidator,
GitSourceReader, FunctionBuildService, RuntimeContainerSpec, Placeholders).
"""

import argparse
import datetime as dt
import json
import os
import re
import shlex
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the scripts say how to install it
    sys.exit("PyYAML is missing: pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = "/runr/runr-wrapper"
RESERVED_IDS = {"docker", "caas", "custom", "runtime", "runtimes", "validate"}
TRIGGERS = ["http", "schedule", "queue"]
PLACEHOLDERS = ["version", "image", "file", "handler", "Handler", "mode", "trigger", "semver"]
FILE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
MAX_FILES = 50
MAX_FILES_BYTES = 900 * 1024
MAX_MANIFEST_BYTES = 262144
MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_RUNTIMES = 200
CATALOG_MEDIA_TYPE = "application/vnd.runr.runtime-catalog.v1+json"


class Problems(list):
    def add(self, path, message):
        self.append((path or "/", message))


# ── Reading ────────────────────────────────────────────────────────────────────


def read_text(path):
    return Path(path).read_bytes().decode("utf-8").replace("\r\n", "\n")


def load_manifest(path):
    """The manifest as RunR decodes it: YAML 1.1, so an unquoted 5.4 is a number and an unquoted
    2027-06-30 a date - neither is the string the schema wants."""
    path = Path(path)
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError(f"is larger than {MAX_MANIFEST_BYTES} bytes")
    try:
        data = yaml.safe_load(read_text(path))
    except yaml.YAMLError as e:
        raise ValueError(f"is not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("must be a mapping (runtime.yaml)")
    return data


def inline_refs(data, runtime_dir):
    """build.files values "@name" replaced by the file next to the manifest (GitSourceReader)."""
    problems = Problems()
    files = (data.get("build") or {}).get("files") or {}
    if not isinstance(files, dict):
        return problems
    runtime_dir = Path(runtime_dir).resolve()

    def inline(name, value):
        if not isinstance(value, str) or not value.startswith("@"):
            return value
        target = runtime_dir / value[1:]
        try:
            real = target.resolve(strict=True)
        except (FileNotFoundError, OSError):
            real = None
        if real is None or target.is_symlink() or not real.is_file() or runtime_dir not in real.parents:
            problems.add(f"/build/files/{name}", f"references {value}, which is not a file next to the manifest")
            return value
        return read_text(real)

    for name, content in list(files.items()):
        if isinstance(content, dict):
            files[name] = {k: inline(name, v) for k, v in content.items()}
        else:
            files[name] = inline(name, content)
    return problems


def build_context_files(runtime_dir):
    """The files RunR uploads for a runtime with a dockerfile version: every regular file of its
    directory but runtime.yaml (GitSourceReader). Returns (files, problems, skipped)."""
    problems, files, skipped, total = Problems(), {}, [], 0
    for entry in sorted(Path(runtime_dir).iterdir(), key=lambda p: p.name):
        if entry.name == "runtime.yaml":
            continue
        if entry.is_symlink() or not entry.is_file():
            skipped.append(entry.name)
            continue
        if not FILE_NAME.match(entry.name):
            problems.add("/", f"{entry.name}: file names next to a dockerfile runtime may only use letters, digits, '.', '-' and '_'")
            continue
        raw = entry.read_bytes()
        total += len(raw)
        try:
            files[entry.name] = raw.decode("utf-8")
        except UnicodeDecodeError:
            problems.add("/", f"{entry.name}: not UTF-8 text; a catalog carries text files only (use a git version for binary build files)")
    if len(files) > MAX_FILES or total > MAX_FILES_BYTES:
        problems.add("/", f"the files next to the manifest exceed {MAX_FILES} files or {MAX_FILES_BYTES // 1024} KB (use a git version for larger build contexts)")
    return files, problems, skipped


def uses_dockerfile(data):
    return any(isinstance(v, dict) and "dockerfile" in v for v in data.get("versions") or [])


# ── Checks ─────────────────────────────────────────────────────────────────────


def type_problems(value, path=""):
    """Values YAML turned into something JSON has no word for (dates), with the way out."""
    problems = Problems()
    if isinstance(value, (dt.date, dt.datetime)):
        problems.add(path, f"is a YAML date; quote it: \"{value.isoformat()}\"")
    elif isinstance(value, dict):
        for k, v in value.items():
            problems.extend(type_problems(v, f"{path}/{k}"))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            problems.extend(type_problems(v, f"{path}/{i}"))
    return problems


def schema_problems(data, schema_file):
    try:
        import jsonschema
    except ImportError:
        return None
    schema = json.loads(Path(schema_file).read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    validator = cls(schema, format_checker=cls.FORMAT_CHECKER if hasattr(cls, "FORMAT_CHECKER") else jsonschema.FormatChecker())
    problems = Problems()
    for error in sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path]):
        problems.add("/" + "/".join(str(p) for p in error.absolute_path), error.message)
    return problems


def semantic_problems(data, runtime_dir):
    """RunR's rules beyond the schema (ManifestValidator, a catalog manifest) and the git source's
    rules (GitSourceReader, RuntimeSourceSync), plus this sample's own conventions (warnings)."""
    problems, warnings = Problems(), Problems()
    runtime_dir = Path(runtime_dir)
    rid = data.get("id")

    if rid in RESERVED_IDS:
        problems.add("/id", "is reserved")
    if rid != runtime_dir.name:
        problems.add("/id", f"The id '{rid}' does not match its directory '{runtime_dir.name}'.")
    if "versions" not in data and "versionDiscovery" not in data:
        problems.add("/versions", "is required")

    seen, defaults = set(), 0
    for i, v in enumerate(data.get("versions") or []):
        if not isinstance(v, dict):
            continue
        p = f"/versions/{i}"
        sources = [k for k in ("image", "dockerfile", "git", "upload") if k in v]
        if len(sources) != 1:
            problems.add(p, "needs exactly one of image, dockerfile, git or upload")
        if "buildArgs" in v and "dockerfile" not in v and "git" not in v:
            problems.add(f"{p}/buildArgs", "only applies to a dockerfile or git version")
        if "dockerfile" in v:
            target = runtime_dir / str(v["dockerfile"])
            if target.is_symlink() or not target.is_file():
                problems.add(f"{p}/dockerfile", f"names {v['dockerfile']}, which is not among the uploaded files (the files next to the manifest)")
        if v.get("version") in seen:
            problems.add(f"{p}/version", "is listed twice")
        seen.add(v.get("version"))
        defaults += 1 if v.get("default") else 0
        problems.extend(lifecycle_problems(v, p))
        if "eol" not in v:
            warnings.add(f"{p}/eol", "no end of life: RunR sends no end-of-life reminders for this version")
    problems.extend(lifecycle_problems(data, ""))
    if data.get("replacement") is not None and data.get("replacement") == rid:
        problems.add("/replacement", "names the runtime itself")
    if defaults > 1:
        problems.add("/versions", "has more than one default version")
    elif defaults == 0 and data.get("versions"):
        problems.add("/versions", "marks no version default: true (this sample marks exactly one; RunR would take the last)")

    run = data.get("run") or {}
    mode = run.get("mode")
    triggers = data.get("triggers") or TRIGGERS
    if mode in ("exec", "persistent") and not run.get("command"):
        problems.add("/run/command", f"is required in {mode} mode")
    if mode == "http" and "schedule" in triggers and run.get("schedule", "invoke") == "invoke" and not run.get("command"):
        problems.add("/run/command", "is required for the schedule trigger of an http runtime (the wrapper starts the server); or set run.schedule: native")
    if "schedule" in run and mode != "http":
        problems.add("/run/schedule", "only applies to http mode")
    if "input" in run and mode != "exec":
        problems.add("/run/input", "only applies to exec mode")
    if "workers" in run and mode != "persistent":
        problems.add("/run/workers", "only applies to persistent mode")

    pattern = (data.get("handler") or {}).get("pattern")
    if isinstance(pattern, str):
        try:
            re.compile(pattern.replace("{handler}", "handler"))
        except re.error as e:
            warnings.add("/handler/pattern", f"does not compile with Python's re ({e}); RunR uses PCRE, check it there")

    extensions = data.get("extensions") or [""]
    handler_file = f"{(data.get('handler') or {}).get('file', 'main')}.{extensions[0]}"
    deps = data.get("dependencies") or {}
    if deps.get("file") == handler_file:
        problems.add("/dependencies/file", "must differ from the handler file")

    if "runtimeClass" in data:
        warnings.add("/runtimeClass", "only platform runtimes may set it: a source an organization connects is refused")

    if uses_dockerfile(data):
        _, file_problems, skipped = build_context_files(runtime_dir)
        problems.extend(file_problems)
        for name in skipped:
            warnings.add("/", f"{name}: not a regular file, not part of the build context RunR uploads")
    return problems, warnings


def lifecycle_problems(item, path):
    problems = Problems()
    deprecated, sunset = item.get("deprecated"), item.get("sunset")
    if isinstance(deprecated, str) and isinstance(sunset, str) and sunset < deprecated:
        problems.add(f"{path}/sunset", f"is before the deprecation ({deprecated})")
    return problems


def check_manifest(path, schema_file=None):
    """(manifest with @file references inlined or None, problems, warnings)"""
    path = Path(path)
    try:
        data = load_manifest(path)
    except ValueError as e:
        return None, Problems([("/", str(e))]), Problems()
    problems = type_problems(data)
    for i, v in enumerate(data.get("versions") or []):
        if isinstance(v, dict) and isinstance(v.get("version"), (int, float)) and not isinstance(v.get("version"), bool):
            problems.add(f"/versions/{i}/version", f"is a YAML number; quote it: \"{v['version']}\"")
    problems.extend(inline_refs(data, path.parent))
    if schema_file:
        found = schema_problems(data, schema_file)
        if found:
            problems.extend(found)
    semantic, warnings = semantic_problems(data, path.parent)
    problems.extend(semantic)
    return data, problems, warnings


def cmd_check(args):
    failed = False
    if args.schema:
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            print("warning: no jsonschema module: the schema was not checked here (pip install -r requirements.txt)", file=sys.stderr)
    for manifest in args.manifests:
        rel = os.path.relpath(manifest, ROOT)
        _, problems, warnings = check_manifest(manifest, args.schema)
        for p, message in warnings:
            print(f"warning: {rel}: {p}: {message}")
        for p, message in problems:
            print(f"{rel}: {p}: {message}")
        if problems:
            failed = True
        else:
            print(f"{rel}: valid")
    return 1 if failed else 0


# ── Normalized views (ManifestValidator::normalize) ─────────────────────────────


def manifest_or_exit(path):
    data, problems, _ = check_manifest(path)
    if problems:
        for p, message in problems:
            print(f"{path}: {p}: {message}", file=sys.stderr)
        sys.exit(1)
    return data


def find_version(data, label):
    for v in data.get("versions") or []:
        if v.get("version") == label:
            return v
    sys.exit(f"{data.get('id')}: no version {label!r} (versions: {', '.join(v.get('version') for v in data.get('versions') or [])})")


def run_section(data):
    run = dict(data["run"])
    mode = run["mode"]
    run.setdefault("schedule", "invoke" if mode == "http" else None)
    run.setdefault("input", "json" if mode == "exec" else None)
    run.setdefault("workers", 1 if mode == "persistent" else None)
    run.setdefault("env", {})
    run.setdefault("command", None)
    return run


def build_section(data):
    build = data.get("build")
    if build is None:
        return None
    return {"image": "{image}", "runImage": "{image}", "source": True, "files": {}, **build}


def deps_section(data):
    deps = data.get("dependencies")
    if deps is None:
        return None
    return {"template": None, "image": "{image}", "copy": [], "env": {}, **deps}


def handler_names(data):
    handler = data.get("handler") or {}
    return f"{handler.get('file', 'main')}.{data['extensions'][0]}", handler.get("function", "handler")


def needs_wrapper(data, trigger):
    run = run_section(data)
    if run["mode"] in ("exec", "persistent"):
        return True
    return trigger == "schedule" and run["schedule"] == "invoke"


def uses_wrapper(data):
    return any(needs_wrapper(data, t) for t in data.get("triggers") or TRIGGERS)


def render(text, values):
    """Placeholders::render: {if:NAME}…{end}, then the known names; anything else stays."""
    values = {k: "" if v is None else str(v) for k, v in values.items()}
    version = values.get("version", "")
    values.setdefault("semver", version if re.fullmatch(r"\d+\.\d+(\.\d+)?", version) else "")
    if "Handler" not in values and "handler" in values:
        values["Handler"] = values["handler"][:1].upper() + values["handler"][1:]
    names = "|".join(PLACEHOLDERS)
    text = re.sub(r"\{if:(" + names + r")\}(.*?)\{end\}", lambda m: m.group(2) if values.get(m.group(1), "") else "", text, flags=re.S)
    return re.sub(r"\{(" + names + r")\}", lambda m: values.get(m.group(1), ""), text)


def wrapper_env(data, command):
    run = run_section(data)
    env = {
        "RUNR_WRAPPER_MODE": "http" if run["mode"] == "http" else run["mode"],
        "RUNR_COMMAND": json.dumps(command or [], ensure_ascii=False, separators=(",", ":")),
    }
    if run["mode"] == "exec":
        env["RUNR_INPUT"] = run["input"]
    if run["mode"] == "persistent":
        env["RUNR_WORKERS"] = str(run["workers"])
    return env


def env_value(value):
    """FunctionBuildService::envValue: quoted unless plain."""
    if re.fullmatch(r"[A-Za-z0-9_./:,=@+-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def sh_assign(name, value):
    if isinstance(value, list):
        return f"{name}=(" + " ".join(shlex.quote(str(v)) for v in value) + ")"
    if isinstance(value, bool):
        value = "1" if value else "0"
    return f"{name}={shlex.quote('' if value is None else str(value))}"


# ── Contract test plan ─────────────────────────────────────────────────────────


def cmd_inline(args):
    print(json.dumps(manifest_or_exit(args.manifest), indent=2, ensure_ascii=False))
    return 0


def cmd_versions(args):
    data = manifest_or_exit(args.manifest)
    for v in data.get("versions") or []:
        source = next(k for k in ("image", "dockerfile", "git", "upload") if k in v)
        print(v["version"], source, "default" if v.get("default") else "-")
    return 0


def cmd_plan(args):
    data = manifest_or_exit(args.manifest)
    v = find_version(data, args.version)
    run = run_section(data)
    git = v.get("git") or {}
    filename, handler = handler_names(data)
    deps = deps_section(data)
    build_args = []
    for key, value in (v.get("buildArgs") or {}).items():
        build_args += ["--build-arg", f"{key}={value}"]
    lines = [
        sh_assign("RUNTIME_ID", data["id"]),
        sh_assign("RUNTIME_NAME", data["name"]),
        sh_assign("RUN_MODE", run["mode"]),
        sh_assign("RUN_SCHEDULE", run["schedule"]),
        sh_assign("SOURCE_KIND", next(k for k in ("image", "dockerfile", "git", "upload") if k in v)),
        sh_assign("SOURCE_IMAGE", v.get("image")),
        sh_assign("SOURCE_DOCKERFILE", v.get("dockerfile") or git.get("dockerfile") or "Dockerfile"),
        sh_assign("SOURCE_GIT_URL", git.get("url")),
        sh_assign("SOURCE_GIT_REF", git.get("ref")),
        sh_assign("SOURCE_GIT_PATH", git.get("path")),
        sh_assign("SOURCE_UPLOAD", v.get("upload")),
        sh_assign("BUILD_ARGS", build_args),
        sh_assign("HAS_BUILD", data.get("build") is not None),
        sh_assign("HAS_DEPS", deps is not None),
        sh_assign("DEPS_FILE", deps["file"] if deps else ""),
        sh_assign("USES_WRAPPER", uses_wrapper(data)),
        sh_assign("TRIGGERS", data.get("triggers") or TRIGGERS),
        sh_assign("HANDLER_FILE", filename),
        sh_assign("HANDLER_NAME", handler),
        sh_assign("VERSION_EOL", v.get("eol")),
        sh_assign("VERSION_DEPRECATED", v.get("deprecated")),
        sh_assign("VERSION_SUNSET", v.get("sunset")),
    ]
    print("\n".join(lines))
    return 0


def cmd_code(args):
    """The starter files (RuntimeManifest::templateFiles): XaaS the entry file, FaaS every
    template file plus the dependency template."""
    data = manifest_or_exit(args.manifest)
    filename, handler = handler_names(data)
    values = {"file": filename, "handler": handler}
    files = (data.get("template") or {}).get("files") or {"{file}": ""}
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        rendered_name = render(name, values)
        if not args.faas and rendered_name != filename:
            continue
        (out / rendered_name).write_text(render(content, values), encoding="utf-8", newline="\n")
    deps = deps_section(data)
    if args.faas and deps and deps.get("template") is not None:
        (out / deps["file"]).write_text(deps["template"], encoding="utf-8", newline="\n")
    return 0


def cmd_dockerfile(args):
    """FunctionBuildService::dockerfile: the two-stage Dockerfile of a function build. The
    wrapper comes from its image (RunR copies it into the build context first: same file)."""
    data = manifest_or_exit(args.manifest)
    find_version(data, args.version)
    mode = "schedule" if args.trigger == "schedule" else "http"
    filename, handler = handler_names(data)
    values = {"version": args.version, "image": args.image, "file": filename, "handler": handler,
              "mode": mode, "trigger": args.trigger}
    build, deps, run = build_section(data), deps_section(data), run_section(data)
    code = Path(args.codedir)

    # build.files next to the code; a {http, schedule} pair picks the trigger's variant
    for name, content in (build or {}).get("files", {}).items():
        text = content[mode] if isinstance(content, dict) else content
        (code / name).write_text(text, encoding="utf-8", newline="\n")
    files = {p.name for p in code.iterdir()}

    has_deps = deps is not None and (build is None or deps["file"] in files)
    steps = []
    if build:
        steps.append("mkdir -p /build/out")
    if has_deps:
        steps.append(render(deps["install"], values))
    if build:
        steps.append(render(build["command"], values))

    stage_image = render((build or {}).get("image") or (deps or {}).get("image") or "{image}", values)
    run_image = render(build["runImage"], values) if build else args.image

    lines = [
        f"FROM {stage_image} AS build",
        "WORKDIR /build/code",
        "COPY code/ /build/code/",
        "RUN " + " && ".join(steps or ["true"]),
        "",
        f"FROM {run_image}",
        "WORKDIR /app",
    ]
    if not build or build["source"]:
        lines.append("COPY code/ /app/code/")
    if has_deps:
        for copy in deps["copy"]:
            lines.append(f"COPY --from=build {copy['from']} {copy['to']}")
    if build:
        lines.append("COPY --from=build /build/out/ /app/")
    if uses_wrapper(data):
        lines.append(f"COPY --from={args.wrapper_image} /runr-wrapper {WRAPPER_PATH}")

    env = {"RUNR_MODE": mode, "RUNR_HANDLER": handler, "RUNR_FILENAME": filename}
    if has_deps:
        env.update({k: render(v, values) for k, v in deps["env"].items() if k not in env})
    env.update({k: render(v, values) for k, v in run["env"].items() if k not in env})
    command = [render(part, values) for part in run["command"]] if run["command"] else None
    if run["mode"] != "http":
        env.update({k: v for k, v in wrapper_env(data, command).items() if k not in env})
    lines.append("ENV " + " ".join(f"{k}={env_value(v)}" for k, v in env.items()))
    lines.append("EXPOSE 8080")
    entry = command if run["mode"] == "http" else [WRAPPER_PATH]
    if entry:
        lines.append("ENTRYPOINT " + json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
    print("\n".join(lines))
    return 0


def cmd_container(args):
    """RuntimeContainerSpec::apply: the function container's environment (an --env-file) and its
    command (CONTAINER_COMMAND; empty: the image's entrypoint)."""
    data = manifest_or_exit(args.manifest)
    find_version(data, args.version)
    trigger = args.trigger
    mode = "schedule" if trigger == "schedule" else "http"
    filename, handler = handler_names(data)
    values = {"version": args.version, "file": filename, "handler": handler, "mode": mode, "trigger": trigger}
    run = run_section(data)
    env = {
        "RUNR_MODE": mode,
        "RUNR_TRIGGER": trigger,
        "RUNR_HANDLER": handler,
        "RUNR_FILENAME": filename,
        "RUNR_CODE_DIR": "/app/code",
        "RUNR_RUNTIME": data["id"],
        "RUNR_RUNTIME_VERSION": args.version,
        "RUNR_TIMEOUT": str(args.timeout),
    }
    env.update({k: render(v, values) for k, v in run["env"].items() if k not in env})
    command = [render(part, values) for part in run["command"]] if run["command"] else None
    container_command = []
    if needs_wrapper(data, trigger):
        env.update({k: v for k, v in wrapper_env(data, command).items() if k not in env})
        container_command = [WRAPPER_PATH]
    elif not args.built and command:
        container_command = command
    if trigger != "schedule":
        env["PORT"] = "8080"  # Knative sets it for the Service; a CronJob's wrapper defaults to 8080
    with open(args.envfile, "w", encoding="utf-8", newline="\n") as f:
        for k, v in env.items():
            if "\n" in v:
                sys.exit(f"{k}: a value with a newline cannot go through docker --env-file")
            f.write(f"{k}={v}\n")
    print(sh_assign("CONTAINER_COMMAND", container_command))
    return 0


# ── Catalog ───────────────────────────────────────────────────────────────────


def runtime_manifests(root, path="runtimes"):
    base = Path(root) / path
    return sorted(base.glob("*/runtime.yaml"), key=lambda p: p.as_posix())


def now_rfc3339():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_catalog(args):
    root = Path(args.root).resolve()
    manifests = runtime_manifests(root, args.path)
    if not manifests:
        sys.exit(f"no {args.path}/<id>/runtime.yaml under {root}")
    if len(manifests) > MAX_RUNTIMES:
        sys.exit(f"more than {MAX_RUNTIMES} runtimes")
    failed, runtimes = False, []
    for manifest in manifests:
        rel = manifest.relative_to(root).as_posix()
        data, problems, _ = check_manifest(manifest, root / "schema" / "runtime.schema.json")
        if problems:
            failed = True
            for p, message in problems:
                print(f"{rel}: {p}: {message}", file=sys.stderr)
            continue
        entry = {"path": rel, "manifest": data}
        if uses_dockerfile(data):
            entry["files"], _, _ = build_context_files(manifest.parent)
        runtimes.append(entry)
    if failed:
        return 1

    payload = {
        "schema": 1,
        "generated_at": now_rfc3339(),
        "source": {"repository": args.repository, "revision": args.revision},
        "runtimes": runtimes,
    }
    if args.patches:
        feed = json.loads(Path(args.patches).read_text(encoding="utf-8"))
        problems = patch_problems(feed, root)
        if problems:
            for message in problems:
                print(f"{args.patches}: {message}", file=sys.stderr)
            return 1
        payload["patches"] = feed.get("patches") or []

    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if len(text.encode("utf-8")) > MAX_CATALOG_BYTES:
        sys.exit(f"the catalog is larger than {MAX_CATALOG_BYTES} bytes (RunR refuses it)")
    problems = schema_problems(payload, root / "schema" / "runtime-catalog.v1.json")
    if problems is None:
        sys.exit("the jsonschema module is missing: pip install -r requirements.txt")
    if problems:
        for p, message in problems:
            print(f"catalog: {p}: {message}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


# ── Patches ───────────────────────────────────────────────────────────────────


def tag_version(tag):
    """lib/feed.jq tag_version of BRF-Tech/runr-runtimes: 5.4.8-r1 → 5.4."""
    tag = re.sub(r"-r[0-9]+$", "", tag)
    m = re.match(r"^(?P<maj>[0-9]+)\.(?P<min>[0-9]+)[0-9A-Za-z.]*(?P<var>-.+)?$", tag)
    return f"{m['maj']}.{m['min']}{m['var'] or ''}" if m else tag


def repository_of(image):
    image = image.split("@", 1)[0]
    last = image.rsplit("/", 1)
    if ":" in last[-1]:
        image = image[: image.rindex(":")]
    return image


def patch_problems(feed, root):
    """The patch feed's schema (schema/patch-feed.v1.json), the rules of BRF's feed (feed.jq
    feed_problems) and the rule of a source's feed: patches of this source's own runtimes only,
    for versions whose image RunR pulls (image versions)."""
    root = Path(root)
    messages = []
    found = schema_problems(feed, root / "schema" / "patch-feed.v1.json")
    if found is None:
        return ["the jsonschema module is missing: pip install -r requirements.txt"]
    messages += [f"{p}: {m}" for p, m in found]
    if messages:
        return messages

    runtimes = {}
    for manifest in runtime_manifests(root):
        try:
            data = load_manifest(manifest)
        except ValueError:
            continue
        runtimes[data.get("id")] = {v.get("version"): v for v in data.get("versions") or [] if isinstance(v, dict)}

    patches = feed.get("patches") or []
    ids, per_version = set(), {}
    for i, patch in enumerate(patches):
        where = f"/patches/{i} ({patch['id']})"
        if patch["id"] in ids:
            messages.append(f"{where}: duplicate patch id")
        ids.add(patch["id"])
        if patch["id"] != f"{patch['runtime']}-{patch['version']}-{patch['tag']}":
            messages.append(f"{where}: the id is not <runtime>-<version>-<tag> ({patch['runtime']}-{patch['version']}-{patch['tag']})")
        if tag_version(patch["tag"]) != patch["version"]:
            messages.append(f"{where}: {patch['tag']} is not a patch tag of {patch['version']}")
        if patch.get("deadline_at") and patch["deadline_at"] < patch["published_at"]:
            messages.append(f"{where}: deadline_at is before published_at")
        version = runtimes.get(patch["runtime"], {}).get(patch["version"])
        if patch["runtime"] not in runtimes:
            messages.append(f"{where}: runtime {patch['runtime']} is not a runtime of this repository (RunR ignores it)")
        elif version is None:
            messages.append(f"{where}: {patch['runtime']} has no version {patch['version']}")
        elif "image" not in version:
            messages.append(f"{where}: {patch['runtime']} {patch['version']} is not an image version (RunR builds it itself, a patch cannot apply)")
        elif repository_of(version["image"]) != patch["image"]:
            messages.append(f"{where}: image {patch['image']} is not the repository of {version['image']}")
        key = (patch["runtime"], patch["version"])
        per_version[key] = per_version.get(key, 0) + 1
    for (runtime, version), count in per_version.items():
        if count > 5:
            messages.append(f"{runtime} {version}: {count} patches (at most 5: RunR pins the newest five)")
    if feed.get("platform"):
        messages.append("/platform: a source's feed announces no RunR releases; leave it empty")
    return messages


def cmd_patches(args):
    feed = json.loads(Path(args.file).read_text(encoding="utf-8"))
    messages = patch_problems(feed, args.root)
    rel = os.path.relpath(args.file, args.root)
    for message in messages:
        print(f"{rel}: {message}")
    if not messages:
        print(f"{rel}: valid ({len(feed.get('patches') or [])} patches)")
    return 1 if messages else 0


def cmd_feed(args):
    """The patch feed to publish: patches.json checked, generated_at set to now."""
    feed = json.loads(Path(args.file).read_text(encoding="utf-8"))
    messages = patch_problems(feed, args.root)
    if messages:
        for message in messages:
            print(f"{args.file}: {message}", file=sys.stderr)
        return 1
    feed["generated_at"] = now_rfc3339()
    feed.setdefault("platform", [])
    text = json.dumps(feed, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


def cmd_matrix(args):
    """The CI matrix: one {runtime, version, source} per version of every runtime."""
    entries = []
    for manifest in runtime_manifests(args.root):
        data = manifest_or_exit(manifest)
        for v in data.get("versions") or []:
            source = next(k for k in ("image", "dockerfile", "git", "upload") if k in v)
            if args.images and source != "image":
                continue
            entries.append({"runtime": data["id"], "version": v["version"], "source": source, "image": v.get("image", "")})
    print(json.dumps(entries, separators=(",", ":")))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check")
    p.add_argument("--schema")
    p.add_argument("manifests", nargs="+")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("inline")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_inline)

    p = sub.add_parser("versions")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_versions)

    p = sub.add_parser("plan")
    p.add_argument("manifest")
    p.add_argument("version")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("code")
    p.add_argument("manifest")
    p.add_argument("dir")
    p.add_argument("--faas", action="store_true")
    p.set_defaults(func=cmd_code)

    p = sub.add_parser("dockerfile")
    p.add_argument("manifest")
    p.add_argument("version")
    p.add_argument("image")
    p.add_argument("trigger", choices=TRIGGERS)
    p.add_argument("codedir")
    p.add_argument("wrapper_image")
    p.set_defaults(func=cmd_dockerfile)

    p = sub.add_parser("container")
    p.add_argument("manifest")
    p.add_argument("version")
    p.add_argument("trigger", choices=TRIGGERS)
    p.add_argument("envfile")
    p.add_argument("--built", action="store_true")
    p.add_argument("--timeout", type=int, default=30)
    p.set_defaults(func=cmd_container)

    p = sub.add_parser("catalog")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--path", default="runtimes")
    p.add_argument("--repository", required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--patches")
    p.add_argument("--out")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("patches")
    p.add_argument("file")
    p.add_argument("--root", default=str(ROOT))
    p.set_defaults(func=cmd_patches)

    p = sub.add_parser("feed")
    p.add_argument("file")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--out")
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser("matrix")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--images", action="store_true", help="image versions only (what CI publishes)")
    p.set_defaults(func=cmd_matrix)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
