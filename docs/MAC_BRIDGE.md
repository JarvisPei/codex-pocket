# Codex Pocket macOS bridge

This bridge exposes a narrow localhost-only API and mobile page for browsing
Codex history and safely controlling tasks owned by the ChatGPT/Codex Desktop
process. Phone refreshes, disconnects, and bridge restarts do not own or cancel
Desktop turns.

## Start

Generate a high-entropy token and keep it outside shell history in normal use:

```sh
export MOBILE_CODEX_BRIDGE_TOKEN="<at-least-32-random-characters>"
python3 mac_bridge.py
```

For unattended startup, prefer a mode-0600 token file:

```sh
python3 mac_bridge.py --token-file ~/.codex/mobile-codex-bridge/token
```

On macOS, Keychain is preferred:

```sh
python3 mac_bridge.py --keychain-service mobile-codex-bridge
```

To start the Keychain-backed bridge automatically after login:

```sh
zsh scripts/install-mac-bridge-launch-agent.sh
```

The installer writes a user LaunchAgent at
`~/Library/LaunchAgents/com.local.mobile-codex-bridge.plist`. Logs are kept in
`~/Library/Logs/mobile-codex-bridge.log` and
`~/Library/Logs/mobile-codex-bridge.error.log`.

It also builds `~/Applications/MobileCodexBridgeHelper.app`. Grant
Accessibility permission to this dedicated helper when macOS asks. The helper
contains only the narrow Codex task-title and semantic Stop-button operations;
the Python interpreter does not need Accessibility permission. Re-running the
installer reuses an unchanged helper so its macOS permission remains valid. If
the helper source changes, macOS may require removing the old Accessibility
entry and adding the rebuilt app again because its local code signature changed.

The server refuses non-loopback binds. Put Tailscale Serve or another reviewed
private reverse proxy in front of `127.0.0.1:4317`; do not bind it directly to a
LAN or public interface.

The process that starts the bridge needs macOS Accessibility permission because
the bridge invokes `scripts/codex-ax.swift`.

Open `http://127.0.0.1:4317` on the Mac, or expose that loopback URL through
Tailscale Serve for the Android browser. After single-use pairing, the page
keeps its individually revocable device credential in browser `localStorage`;
it never persists the Keychain master token.

## Stored Codex task history

The bridge starts the Codex Desktop bundle's version-matched
`codex app-server --stdio` as a private child process for bounded history reads.
New phone instructions are not executed by that child: they are deep-linked to
the real Desktop task and submitted through its semantic Accessibility controls.
The raw app-server transport is never exposed on HTTP or Tailscale. The
authenticated mobile API exposes only:

- `GET /api/codex/threads` for a bounded, summarized task list.
- `GET /api/codex/threads/<id>` for up to the latest 60 stored turns.
- `POST /api/codex/threads` to create a persisted Desktop task in a selected
  Project or in Recents, then submit its first instruction through Desktop.
- `POST /api/codex/threads/<id>/turn` to navigate Desktop to the exact thread,
  verify its title, and submit one message with up to four device-owned attachments.
- `POST /api/attachments` and `DELETE /api/attachments/<id>` for bounded,
  per-device temporary uploads. Files are limited to 20 MB and expire after one hour.
- `POST /api/codex/threads/<id>/continue` to press Desktop Continue only when the
  latest persisted turn is still `interrupted`.
- Legacy managed-run read/interrupt/request routes remain temporarily available
  only for runs created before the Desktop-dispatch migration.

The history serializer includes user/agent messages plus bounded command and
file-change summaries. It excludes raw reasoning and truncates large fields.
On the client, agent text is rendered without `innerHTML` as a safe Markdown
subset. Remote links are restricted to HTTP(S) and mailto; local file
references remain non-navigating labels. Code blocks and tables scroll within
their cards on narrow screens. Each contiguous activity section groups command,
file-change, plan, tool-call, browser, and compaction entries into a one-line
`Working`/`Worked` summary at the correct position between messages. Active work
stays expanded; completed work collapses without losing the user's manual
choice. On mobile, a separate touch target on the right provides a draggable
scroll thumb and track-based page jumps.
Reading a thread uses `thread/read` and never resumes, starts, or modifies it.
History responses use browser-negotiated gzip compression. The mobile page
initially requests the newest 30 turns, can explicitly load up to 60, renders
them in one DOM batch, and keeps a small one-minute memory/cache window for
fast task switching. Starting a Desktop turn invalidates that task's cached
history. While
the selected Desktop task is running, the phone refreshes its stored history at
a bounded five-second interval; completion triggers a final fresh read. The
conversation-header refresh button bypasses the history cache and
refreshes only the selected task; it does not reload the project list, page
assets, pairing state, or the current composer draft.
The selected-task badge distinguishes `运行中`, `已暂停`, `已完成`, historical,
failed, and unknown states. An app-server turn whose terminal status is
`interrupted` is resumable rather than complete: with an empty composer the
action becomes a Continue triangle and presses Desktop's semantic Continue/Send
control without adding a user message. Typed text still starts a normal
follow-up turn. The continue endpoint re-reads the thread and refuses the
operation unless its latest persisted turn is still `interrupted`.

## Android system notifications

Notifications are opt-in per browser. The drawer button requests the browser's
notification permission from a direct user gesture and registers `/sw.js` on
the same origin. Once enabled, the page refreshes the bounded thread summary at
most once every ten seconds and notifies only on a new completed, interrupted,
or failed task update, or when the foreground Desktop task exposes a new
approval/user-input request. Notification bodies contain the task title but no
response text, tool output, credentials, or attachment data. Clicking a
notification focuses an existing Codex Pocket window and opens that thread, or
opens `/?thread=<id>` when no client window exists.

This first-party implementation does not use a third-party Web Push service.
It therefore requires the Codex Pocket page to remain open or retained as a
background browser/PWA page. Fully terminating the Android browser stops its
polling; supporting that case later would require a separate push subscription,
VAPID key lifecycle, and push delivery service.
Completed turns mark their last agent message as
`Codex · 最终回复` even when the app-server omits the message phase. Stop-button
taps immediately show feedback; Desktop interruption still requires explicit
confirmation; legacy managed interruption remains supported for old runs.
Starting a turn is a separate POST operation. The helper accepts only a UUID
thread id and constructs `codex://threads/<id>` itself. After navigation it
requires one exact expected title, zero semantic `Stop` buttons, one empty
`AXTextArea`, and one semantic `Send` button. It refuses to overwrite a Desktop
draft and rechecks task identity immediately before pressing Send. Message text
is supplied over stdin rather than command-line arguments.
Attachment paths are also supplied over stdin, but the Helper resolves each one
and refuses anything outside `~/Library/Application Support/MobileCodexBridge/uploads`.
Files are pasted through the Desktop composer only after task and draft checks;
the Helper waits for an attachment control with the exact filename before Send.

Managed command and file approvals support only `accept`, `decline`, or
`cancel`; the mobile API intentionally omits session-wide and persistent policy
amendments. Raw reasoning, arbitrary JSON-RPC methods, arbitrary Mac file paths,
and general shell access remain unavailable.

Desktop owns every new phone-started turn and newly created task. The private
history app-server still
does not share Desktop's in-memory runtime status, so Accessibility remains the
authority for foreground running/stop state; persisted history is used only for
messages and terminal turn status.

To pair an Android browser without copying the Keychain token into a terminal,
generate a temporary QR code on the Mac:

```sh
swift scripts/create-pairing-qr.swift \
  https://your-mac.your-tailnet.ts.net/ \
  /private/tmp/mobile-codex-pairing.png
```

The QR contains a five-minute, single-use pairing ticket in the URL fragment.
Fragments are not sent in HTTP requests; the page exchanges the ticket for an
individually revocable device credential and immediately clears it from the
address bar. The device credential is kept in browser `localStorage`, so closing
the tab or restarting the phone does not require pairing again. It is scoped to
that device; the Keychain master token never leaves the Mac.

Treat the temporary QR image as a secret and delete it after pairing. List or
revoke paired devices from the Mac without printing any credential:

```sh
python3 scripts/manage-bridge-devices.py list
python3 scripts/manage-bridge-devices.py revoke <device-id>
```

Revocation takes effect on the next mobile status poll (normally within three
seconds). Clearing browser site data also removes that browser's local device
credential, but does not delete its Mac-side registry entry; revoke it from the
Mac when retiring a device.

## Check whether the foreground turn is interruptible

```sh
curl \
  -H "Authorization: Bearer $MOBILE_CODEX_BRIDGE_TOKEN" \
  http://127.0.0.1:4317/api/desktop/interrupt/status
```

Example:

```json
{"ok":true,"taskTitle":"继续项目开发","interruptible":true,"stopCandidates":1}
```

## Interrupt

```sh
curl \
  -X POST \
  -H "Authorization: Bearer $MOBILE_CODEX_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"confirm":true,"threadId":"019fb6fd-68d6-71f1-8d60-ea75a658d0ab","expectedTaskTitle":"继续项目开发"}' \
  http://127.0.0.1:4317/api/desktop/interrupt
```

The bridge first opens `codex://threads/<threadId>`, then refuses the request
unless the focused Desktop window exposes the exact expected task title and
exactly one enabled AX button whose semantic description is `Stop` in the
composer region.

The interrupt request must include the selected task's id and exact title.
Immediately before pressing, the bridge reads the focused window title on both
sides of the Stop probe and refuses if it changed. Switching and bringing the
app forward only happen for a confirmed interrupt; background status polling
never steals focus.

When ChatGPT is covered by another app, AX coordinate hit-testing can report
zero Stop candidates. The mobile page still permits a guarded attempt because
the confirmed action brings ChatGPT forward before checking. The action presses
nothing unless exactly one semantic Stop button is found after activation.

## API safety properties

- Loopback binding only.
- Constant-time Bearer-token comparison.
- Five-minute, single-use pairing tickets.
- A unique random credential per device, stored only as a SHA-256 hash on Mac.
- Individually revocable devices and a mode-0600 device registry.
- Exact UUID deep-link navigation plus foreground task-title revalidation.
- Refusal to overwrite a non-empty Desktop composer draft.
- Unique semantic `AXTextArea`, `Send`, and `Stop` requirements.
- Text input limited to 20,000 characters.
- Allowlisted, size-bounded managed events and interaction requests.
- The Keychain master token stops authorizing control after device migration.
- POST plus explicit confirmation for the interrupt action.
- Foreground task identity comparison immediately before pressing.
- Request-body size limit.
- No CORS opt-in.
- No request-body or Authorization logging.
- Serialized interrupt operations.
- Generic external error messages that do not expose local UI details.

## Tests

```sh
python3 -m unittest discover -s tests -v
```
