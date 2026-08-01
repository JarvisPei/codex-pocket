# Contributing

[中文](CONTRIBUTING.md) | [English](CONTRIBUTING.en.md)

Thanks for helping improve this project. The repository is intentionally kept focused on three goals: beginner-friendly deployment, clear security boundaries, and a minimum necessary code surface.

## Read first

Before contributing, please review:

- `README.md`
- `docs/DEPLOYMENT.md`
- `SECURITY.md`

## Good contribution areas

- bug fixes for phone viewing / chat control
- deployment and self-check script improvements
- beginner-friendly documentation improvements
- security hardening for a single-user self-hosted setup

## Changes that are discouraged by default

- making public internet exposure the default
- loosening auth or device approval without a security review
- turning the repo into a multi-user SaaS
- committing full upstream source, runtime logs, databases, or packaged binaries

## Minimum checks before submitting

Please do at least the following:

1. make sure no real secrets, logs, databases, private hostnames, or personal paths are included
2. run `scripts/check-open-source-tree.ps1`
3. if you changed the override layer, run `scripts/smoke-test-override-flow.ps1`
4. if you changed the desktop tool, run `python -m py_compile mobile_codex_control.py`
5. if you changed docs, keep the Chinese-first entry points consistent

## Pull request guidance

- keep each PR focused on one kind of change
- use clear titles, for example:
  - `fix: repair device approval polling`
  - `docs: clarify first-time Tailscale setup`
- if your change affects the security boundary, explain the risk clearly in the PR

## Security issues

If you found an auth bypass, trusted-device bypass, secret leak, or similar issue, please do not post the full details publicly first.  
Follow the guidance in `SECURITY.md`.

