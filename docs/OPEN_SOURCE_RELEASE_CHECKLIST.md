# Open Source Release Checklist

[中文](OPEN_SOURCE_RELEASE_CHECKLIST.zh-CN.md) | [English](OPEN_SOURCE_RELEASE_CHECKLIST.md)

## Must finish before first public push

- [ ] Confirm this helper repo contains no `vendor/`, `node_modules/`, `dist/`, `build/`, `.runtime/`, or `tmp/` directories
- [ ] Confirm no `.db`, `.sqlite*`, `.log`, `.env`, or packaged `.exe` files are present
- [ ] Confirm no real JWT secret, API key, private key, or auth cookie value appears in text files
- [ ] Replace any personal hostname, tailnet name, local user path, or private IP reference with placeholders
- [ ] Run `powershell -ExecutionPolicy Bypass -File scripts/check-open-source-tree.ps1`
- [ ] Rotate live auth secrets if any previous private build exposed them
- [ ] Verify `README.md` matches the actual setup steps
- [ ] Verify upstream attribution and `LICENSE` are present

## Recommended before tagging a release

- [ ] Run `scripts/smoke-test-override-flow.ps1` against a clean upstream `v1.25.2` checkout
- [ ] Test local start/stop scripts on a fresh machine profile
- [ ] Run `python -m py_compile mobile_codex_control.py`
- [ ] Package the desktop tool with `scripts/package-mobile-codex-control.cmd` if you plan to ship an EXE
- [ ] Add GitHub issue templates and a release note explaining the threat model
