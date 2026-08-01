# Codex Pocket

## Goal

Build a private, Android-friendly web controller for the Codex Desktop app on
macOS. The phone must not sign in to ChatGPT or Codex. Codex Desktop remains the
primary desktop UI and execution host.

## Non-goals

- Do not replace Codex Desktop with a CLI-only workflow.
- Do not expose Codex or its app-server directly to the public internet.
- Do not open a general-purpose remote shell or desktop by default.
- Do not copy ChatGPT/Codex credentials to the phone.

## Confirmed findings

- Upstream `mobileCodexHelper` does not control the Codex Desktop process. Its
  Codex integration uses `@openai/codex-sdk` to start or resume threads owned by
  its own server process.
- OpenAI's built-in Remote feature can control the same desktop projects and
  chats, but the official mobile flow requires the phone to use the same
  ChatGPT account/workspace. That does not meet this project's requirement.
- The installed macOS desktop bundle contains:
  `/Applications/ChatGPT.app/Contents/Resources/codex`
- The installed CLI reports `codex-cli 0.146.0-alpha.3.1` and supports:
  `codex app-server proxy --sock <SOCKET_PATH>`.
- `~/.codex/ipc/ipc.sock` is not an app-server socket. OpenAI's source defines
  it as the private IDE-context router used by TUI `/ide` support.
- The Desktop app starts its app-server as a child process over private
  stdin/stdout pipes:
  `codex -c features.code_mode_host=true app-server --analytics-default-enabled`.
  It does not pass `--listen`, so another local process cannot attach to that
  running app-server.
- A separately managed app-server uses:
  `~/.codex/app-server-control/app-server-control.sock`.
  It shares the same state database and rollout files with Desktop, so it can
  list persisted Desktop threads. It is still a different runtime process and
  cannot observe or answer live approvals owned by Desktop's active process.
- The version-matched app-server schema includes thread listing/reading,
  turn start/steer/interrupt, streamed events, approvals, user-input requests,
  and remote-control methods.
- A version-matched standalone app-server was started on a private Unix socket.
  The read-only probe completed `initialize` and `thread/list` successfully.
  It listed both the original research task
  `019fb6b3-7071-79e0-ba5d-4bec7aced396` and the current project task
  `019fb6fd-68d6-71f1-8d60-ea75a658d0ab`.
- Threads active in Desktop appeared as `notLoaded` in the separate app-server.
  This confirms shared persisted-thread visibility but also confirms that live
  runtime ownership and status are process-local.

## Proposed MVP

1. A localhost-only macOS bridge connects to a managed Codex app-server.
2. The bridge exposes a narrow authenticated HTTP/WebSocket API.
3. An Android browser UI can:
   - list and read Desktop tasks;
   - observe persisted status and output;
   - start or resume tasks owned by the managed app-server;
   - answer explicit approval and user-input requests;
   - interrupt a turn.
4. Remote access goes through Tailscale HTTPS only.
5. First use requires desktop-side device approval; devices can be revoked.

## Implemented on macOS

- A loopback-only bridge is available through tailnet-only Tailscale Serve.
- Android pairs with a five-minute, single-use ticket and receives an
  individually revocable persistent device credential.
- The Keychain master credential never leaves the Mac and no longer authorizes
  control after device migration.
- A dedicated Accessibility helper identifies the visible Desktop task and can
  press exactly one semantic Stop button after task-identity confirmation.
- A private, version-matched `codex app-server --stdio` child provides
  allowlisted `thread/list` and `thread/read` access.
- The mobile UI uses Codex Desktop's project assignments for collapsible
  Projects and puts projectless chats in Recents.
- The mobile UI can browse stored tasks and a bounded, reasoning-free history.
  Agent replies are rendered as a safe DOM-built Markdown subset with mobile
  styling for headings, lists, quotes, links, code blocks, and tables.
- Composer polling never transiently disables the textarea or forces a scroll
  while it is focused, so Android's keyboard remains open during drafting.
- Each turn groups its commands, file changes, tool calls, plans, and web
  searches into one collapsed `工作记录` row; the expanded contents are flat
  details rather than nested disclosures. A touch-sized draggable scroll
  handle supports fast navigation through long mobile histories.
- Idle persisted threads can be resumed from the phone with text-only input.
- Managed agent-message deltas and bounded tool/file events stream into the
  conversation through authenticated polling.
- Managed turns use native `turn/interrupt`.
- Command/file approvals are shown inline with one-shot accept, decline, and
  cancel choices; session-wide approvals are intentionally unavailable.
- Explicit `request_user_input` questions are displayed inline and returned by
  question id.
- Per-thread locking rejects duplicate managed turns. For a task selected in
  Desktop, sending first activates Desktop and verifies the same title twice:
  an idle composer (`Stop` count 0) may be taken over by the managed app-server,
  while an active or ambiguous Desktop turn is rejected.
- If the background Accessibility probe is unavailable during send, the bridge
  activates Desktop and double-checks its title. A different stable title may
  proceed; the same title still requires an idle composer, and an unstable or
  unreadable title is refused with a specific error.

Still pending: attachments, richer permission controls, and broader MCP
elicitation forms.

## Security defaults

- Bind the web application to `127.0.0.1`.
- Never forward the raw Codex app-server socket to the network.
- Keep a strict allowlist of JSON-RPC methods exposed to mobile clients.
- Require a high-entropy session credential in addition to Tailscale.
- Keep hardened mode on and display command/file approval details verbatim.
- Store secrets in macOS Keychain or a mode-0600 file outside the repository.
- Log metadata only; never log credentials or full private task content by
  default.

## First validation

Start the managed app-server and connect locally with:

```sh
/Applications/ChatGPT.app/Contents/Resources/codex \
  app-server daemon start

/Applications/ChatGPT.app/Contents/Resources/codex \
  app-server proxy \
  --sock "$HOME/.codex/app-server-control/app-server-control.sock"
```

The Unix control socket uses a WebSocket upgrade at `/rpc`, not raw JSONL.
After upgrading, send `initialize`, `initialized`, and a read-only
`thread/list` request.
Do not send `turn/start`, approvals, archive, delete, or filesystem mutations
until shared persisted-thread visibility is proven.

The reusable read-only probe is:

```sh
CODEX_APP_SERVER_SOCKET=/absolute/path/to/app-server.sock \
  node scripts/probe-codex-app-server.mjs
```

## Product boundary

Exact live control of a turn currently running inside Codex Desktop is not
available through a public local endpoint. The supported choices are:

- use OpenAI Remote, which requires the phone to use the same ChatGPT account;
- use remote desktop software for exact control of the Desktop UI; or
- run remotely controlled tasks on the managed app-server and let Desktop share
  their persisted history, accepting that live ownership does not transfer
  between the two processes.

## Desktop interruption fallback

macOS Accessibility can interrupt the active Desktop turn without attaching to
Desktop's private app-server:

- The native AX API can inspect the ChatGPT window while a Codex turn is active.
- Coordinate hit-testing within the window's composer region returns an
  `AXButton` whose semantic description is exactly `Stop`.
- The button exposes the standard `AXPress` action.
- `scripts/codex-ax.swift --stop` is guarded: it presses only when exactly one
  semantic Stop button is found; otherwise it refuses.

This controls the task currently visible in the active ChatGPT/Codex window. A
later bridge layer must verify and display that foreground-task limitation
before accepting a remote interrupt.
