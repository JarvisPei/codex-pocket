# Notice

[中文](NOTICE.zh-CN.md) | [English](NOTICE.md)

This repository is not a full replacement for upstream. It is a small helper layer that adds phone-control and hardening behavior on top of upstream.

If you are new to the project, the intended flow is:

- prepare upstream `siteboon/claudecodeui`
- apply the override files from this repository
- follow the README / deployment guide to start and use it

This helper package is designed to be applied on top of the upstream project:

- upstream: `siteboon/claudecodeui`
- tested upstream tag: `v1.25.2`
- upstream license: `GPL-3.0`

This folder intentionally does not ship:

- personal databases
- runtime logs
- packaged binaries
- `node_modules`
- pinned private hostnames or user paths
- full upstream source snapshots outside the minimum override set

This is intentional, to reduce the chance of publishing personal runtime data by accident.

Before publishing this folder, review `docs/OPEN_SOURCE_RELEASE_CHECKLIST.md` and rotate any real secrets that may have been used in earlier private builds.
