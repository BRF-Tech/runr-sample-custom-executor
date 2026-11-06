# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately by e-mail to **security@runr.sh**. Do not open a public
issue, pull request or discussion for a suspected vulnerability.

Include as much of the following as you can:

- the affected part (a runtime image, `runner.lua`, the Zig server or build files, a script, a
  workflow, the signed catalog or patch feed) and the commit or image tag;
- a description of the issue and its impact;
- steps to reproduce, a proof of concept, or the request that shows the problem.

We acknowledge your report within three business days, keep you informed while we work on a fix
and agree a disclosure date with you. A fixed runtime image is published under a new immutable
patch tag and announced in this repository's signed patch feed (README: Ship a patch).

## Scope

In scope: the code and images of this repository - the runtime images
(`ghcr.io/brf-tech/runr/sample-executor-*`), their protocol code, the scripts and the workflows,
and the signed catalog and patch feed published from it.

Vulnerabilities of RunR itself (the API, the console, the universal wrapper, the CLI) belong to
[BRF-Tech/runr](https://github.com/BRF-Tech/runr/blob/main/SECURITY.md); reports go to the same
address. Vulnerabilities of Lua, LuaRocks, Zig or Alpine belong to their projects; tell us too when
an image of this repository needs a rebuild because of one.

## Your fork

A fork publishes its own images, catalog and feed. Replace the address above with your own before
you connect your fork to a RunR that other people use.
