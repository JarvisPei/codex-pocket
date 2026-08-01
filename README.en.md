# Codex Pocket

[中文](README.md)

View and control Codex Desktop on your Mac from an Android phone while keeping Desktop as the real task owner and execution host.

Codex Pocket runs a narrow local bridge on the Mac and exposes it to your own tailnet through Tailscale Serve. The phone does not sign in to ChatGPT and never receives Codex credentials, a general shell, or remote-desktop access.

## Features

- Mirror Codex Desktop Projects and Recents.
- Create a task inside a Project or Recents and hand its first instruction to Desktop.
- Read recent messages, final answers, and position-preserving `Working` / `Worked` activity summaries.
- Send follow-up instructions to existing Desktop tasks.
- Attach files or images from mobile, up to four files and 20 MB per file.
- Distinguish running, paused, and completed tasks and safely stop any running Desktop turn after an ID-based task switch.
- Change the task model, reasoning effort, and Fast service tier.
- Display remaining usage.
- Handle one-shot command/file approvals and structured user questions.
- Pair with a five-minute single-use QR ticket and revoke individual devices.
- Refresh long conversations incrementally with a new-content indicator and draggable scrollbar.

## Architecture

```text
Android browser
      │
      │ Tailscale HTTPS (tailnet only)
      ▼
Tailscale Serve
      │
      │ 127.0.0.1:4317
      ▼
Codex Pocket Bridge
      ├── private, allowlisted Codex app-server for persisted history
      └── dedicated macOS Accessibility Helper for navigation/send/stop
                              │
                              ▼
                       Codex Desktop
```

The bridge always binds to loopback. It never exposes the raw app-server transport, ChatGPT login data, or general macOS input control to the network.

## Requirements

- macOS and Codex Desktop (`/Applications/ChatGPT.app`)
- Apple Command Line Tools to build the dedicated Accessibility Helper
- Tailscale with Serve recommended for tailnet-only HTTPS
- Android or another modern mobile browser

## Install

```sh
git clone https://github.com/JarvisPei/codex-pocket.git
cd codex-pocket
zsh scripts/install-mac-bridge-launch-agent.sh
```

The installer creates and starts a user LaunchAgent, uses a Keychain-backed bridge credential, builds `~/Applications/MobileCodexBridgeHelper.app`, and listens on `127.0.0.1:4317`.

After the first install, allow `Mobile Codex Bridge Helper` in System Settings → Privacy & Security → Accessibility. Low-level bundle, LaunchAgent, and Keychain identifiers intentionally retain the legacy `mobile-codex-bridge` name so existing Accessibility grants remain valid.

Check the local service:

```sh
curl http://127.0.0.1:4317/health
```

## Tailscale access

Expose only the loopback bridge to your tailnet; do not bind the bridge directly to a LAN or public interface:

```sh
tailscale serve --bg http://127.0.0.1:4317
tailscale serve status
```

Open the HTTPS URL assigned by Tailscale.

## Pair a phone

Generate a five-minute, single-use QR code on the Mac:

```sh
swift scripts/create-pairing-qr.swift \
  https://your-mac.your-tailnet.ts.net/ \
  /private/tmp/codex-pocket-pairing.png
```

Scan it on the phone. The device credential is stored in that browser's `localStorage`, so closing the tab or restarting the phone does not require pairing again. Treat the QR image as a temporary secret and delete it after pairing.

List or revoke paired devices:

```sh
python3 scripts/manage-bridge-devices.py list
python3 scripts/manage-bridge-devices.py revoke <device-id>
```

## Security boundary

- Loopback binding only.
- No CORS, general shell, arbitrary JSON-RPC, or raw app-server proxy.
- Each phone receives a separate random credential; the Mac stores only its SHA-256 digest.
- The Keychain master credential never leaves the Mac.
- Desktop send verifies the exact thread id, task title, empty composer, and unique Send control.
- Attachments are isolated per paired device, stored in a mode-`0700` upload directory, expire after one hour, and are rejected by the Helper if their path leaves that directory.
- Stop requires explicit confirmation, an ID-based Desktop task switch, an exact title check, and a unique semantic Stop control.
- Logs contain metadata only, not Authorization values or full prompt bodies.
- Markdown is rendered without `innerHTML`; external links are restricted.

See [the macOS bridge documentation](docs/MAC_BRIDGE.md) for the full protocol and threat boundary.

## Tests

```sh
python3 -m unittest tests.test_mac_bridge
```

## Current limitations

- The helper depends on Codex Desktop's current Accessibility structure and may need updates after major Desktop UI changes.
- This is a private single-user, single-Mac deployment, not a multi-user SaaS service.

## License

GPL-3.0. See [LICENSE](LICENSE).
