# Contributing

Thanks for your interest. Fixes and improvements to the sample executors, the scripts and the
documentation are welcome. This repository follows the
[Code of Conduct of RunR](https://github.com/BRF-Tech/runr/blob/main/CODE_OF_CONDUCT.md). Report
security issues privately, as described in [SECURITY.md](SECURITY.md), never in a public issue.

How runtimes behave inside RunR is documented in
[RunR's runtime docs](https://github.com/BRF-Tech/runr/blob/main/docs/runtimes.md). Bugs and features
of RunR itself belong in [BRF-Tech/runr](https://github.com/BRF-Tech/runr/issues).

## Making a change

1. Fork the repository and create a branch from `main`.
2. Keep the change focused. A change to a runtime's behaviour comes with its test: the starter
   template's answers in `tests/<id>/contract.env`, the dependency case in `tests/<id>/faas/`.
3. Run the checks below, then open a pull request against `main`. CI runs the same checks and the
   contract test of every runtime version; nothing is pushed from a pull request.

```bash
pip install -r requirements.txt
scripts/validate.sh
scripts/contract-test.sh sample-lua
scripts/contract-test.sh sample-zig
scripts/build-catalog.sh -o /tmp/catalog.json
docker run --rm -v "$PWD":/mnt -w /mnt koalaman/shellcheck:v0.11.0 -x scripts/*.sh scripts/lib/*.sh
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:1.7.12 -color
```

Some rules:

- **Versions and dates are quoted strings** in the manifests (`"5.4"`, `"2027-06-30"`).
- **Base images are pinned** by their full tag (`alpine:3.24.3`); a bump is its own commit.
- **Actions are pinned** by commit SHA with the version as a comment.
- **A runtime directory with a `dockerfile` version stays flat and small**: every file in it is
  the build context RunR uploads (at most 50 files, 900 KB).
- **`schema/runtime.schema.json` is RunR's**: refresh it from BRF-Tech/runr, do not edit it here
  (README: The schema copies).

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), for example
`fix(sample-lua): …`, `feat(sample-zig): …`, `docs: …` or `ci: …`.
