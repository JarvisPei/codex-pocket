# Security Policy

[中文](SECURITY.zh-CN.md) | [English](SECURITY.md)

## Who this is for

This file is mainly for:

- people who want to deploy this for their own long-term use
- people who want to publish their own fork safely

If your goal is simply “safe enough for self-use”, remember these three rules first:

1. keep the app bound to `127.0.0.1`
2. prefer a private network such as Tailscale instead of direct public exposure
3. require desktop approval for every new device

## Supported deployment model

The supported model is:

- local app bound to `127.0.0.1`
- reverse proxy in front of the app
- private network access first, ideally Tailscale
- device whitelist plus desktop approval for new phones

This is the model the scripts and docs are designed around.  
If you change it into public exposure, multi-user access, or approval-free login, you are outside the default security boundary of this repository.

## Not recommended

The following are intentionally outside the safe default model:

- exposing the Node app directly to the public internet
- disabling hardened mode without re-auditing the trust model
- committing databases, logs, or build artifacts
- shipping real secrets inside `.env` files or docs

## Secret handling rules

Never commit:

- auth databases
- JWT secrets
- private keys
- live Tailscale hostnames tied to your personal tailnet
- logs containing tokens or user traffic

Before publishing, re-run:

- `scripts/check-open-source-tree.ps1`
- `docs/OPEN_SOURCE_RELEASE_CHECKLIST.md`

If an older private build ever exposed query-string tokens or other session material, rotate the auth database or JWT secret before publishing.

## Reporting guidance

- For non-sensitive bugs, open a public GitHub issue.
- For security-sensitive findings, replace this section with your private contact channel before publishing.
