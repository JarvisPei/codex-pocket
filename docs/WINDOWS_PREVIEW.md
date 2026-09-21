# Windows preview / Windows 开发预览

首次使用请先看[中文上手指南](WINDOWS_QUICKSTART.md) / [English quick start](WINDOWS_QUICKSTART.en.md)。
本页保留开发细节和逐阶段验收记录，不要求新用户顺序执行所有实验命令。

This preview includes a **text-only background backend** and an **opt-in native
existing-task text adapter**. Native text delivery, cross-task switching and
phone pairing/sending have passed initial Windows 11 checks. Guarded native Stop
has also passed desktop and user-confirmed phone checks. Project/Recents creation,
Project multi-file reading and a phone-created Project image task passed initial
checks. Reliable native Resume, installer/autostart
and broader sleep/lock/localization testing remain unfinished. Do not advertise
full Mac/Windows parity or enable experimental controls by default.

这是首版开发预览，不是已经验证完成的 Windows 正式版。可以先测试网页配对、
项目/历史读取、模型/usage 和后台纯文本任务；已有任务的原生文字发送和停止已通过初步实机测试，仍为显式开启的预览。
下方 Windows 专属测试在 Mac 上会跳过，不能把 Mac 测试通过当作 Windows 验收。

## Daily tray entry / 日常托盘入口

`Start Pocket.cmd` installs a local GUI-subsystem bootstrap and desktop shortcut;
the GUI host runs `scripts/windows-pocket-tray.ps1` in an STA Windows PowerShell
engine runspace, without starting powershell.exe/ConsoleHost. This avoids late
console allocation as well as Windows Terminal delegation. Organization execution
policies still apply; RemoteSigned is requested only for this hosted session.
The tray manages one hidden
`windows_pocket.py` runtime containing both the native helper and HTTP bridge.
First launch remembers user-selected Python/CLI paths; subsequent double-clicks
reuse them and existing DPAPI credentials/pairings. The tray supports QR pairing,
status and graceful exit. It does not register autostart or change sleep/Tailscale.

The GUI bootstrap is built from `scripts/windows-pocket-launcher.cs` using the
installed .NET Framework compiler. Automated Windows checks verify the PE GUI
subsystem, exact legacy-shortcut ownership, and a hosted test script's zero console
window/process list. Compilation fixtures do not launch Pocket or mutate user shortcuts.

新入口没有测试启动器的两小时/四小时计时器。重复启动、已有助手和端口冲突不会强行接管；
退出先排空已接受的请求，再关闭自有组件，不关闭 Codex 或发送任务停止指令。
首次路径仍需用户确认，Python 仍需预先安装；原生 Resume、锁屏和安装包不因此宣称支持。
任务栏点击唤起仍需显式 `-EnableTaskbarActivation`，选择保存到本机 launcher 配置。

Windows 11 实机已检查统一进程的 Bridge/助手就绪、拒绝重复 runtime、控制管道关闭后干净退出，
以及托盘启动后 Desktop 进程、凭据和配对未改变。没有为了验收发送模型任务。
下方独立组件命令保留用于开发排错；**日常使用推荐上手指南里的托盘入口，不要同时运行两套。**

## Start here: control existing Desktop tasks / 手机控制已有任务

先走这条原生文字预览流程；下面的后台模式是另一种运行方式，**不要混用启动命令**。
需要 Windows 11、Python 3.11+、已登录的 Codex Desktop、Tailscale。
使用普通用户 PowerShell，不要以管理员运行；本流程不会下载软件或安装自启动服务。

本节采用最小功能配置：已有任务的文字发送、切换、停止；没有开启新建和附件。
需要已验证的新建＋附件流程，请使用上方上手指南中的显式参数；不是功能完全不支持。
原生 Resume 存在已复现的空回复问题，基础启动流程不启用它。

1. 在仓库目录的 PowerShell 中执行（替换 Codex CLI 的实际路径）：

   ```powershell
   $pythonBinary = (py -3 -c "import sys; print(sys.executable)").Trim()
   $codexBinary = 'C:\path\to\native\codex.exe'
   & $pythonBinary windows_bridge.py doctor --codex-binary $codexBinary
   & $pythonBinary windows_bridge.py init
   powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts\windows-start-desktop-helper.ps1 -PythonBinary $pythonBinary -EnableNativeSend -EnableNativeStop
   & $pythonBinary windows_bridge.py serve --codex-binary $codexBinary --native-text-send
   ```

   保持最后的 Bridge 窗口打开；辅助程序最多运行四小时，需手动重新启动。
   已经运行 Bridge 时不要重复启动，端口冲突不会自动替换旧进程。
   `init` 会复用有效凭据；升级或新增手机不需要删除凭据或原配对。

2. 在第二个普通 PowerShell 窗口进入同一仓库，检查各组件：

   ```powershell
   py -3 windows_bridge.py doctor --codex-binary 'C:\path\to\native\codex.exe' --native
   ```

   输出逐项区分 CLI、协议握手、DPAPI 凭据、桌面助手、原生 Stop 和本地 Bridge。
   `FAILED` 后给出对应下一步；`WARNING` 表示暂忙或可选功能限制，不自动修改配置。
   可加 `--json` 获取可分享的结构化结果：不包含密钥、配对链接、对话内容或原始异常。
   退出码 0 表示这些本地检查通过，1 表示存在失败；**不等于手机链路或模型执行已验收**。
   不指定 `--native` 时只验证 CLI 和协议握手。

   如果 SSH 中凭据检查失败，请在已登录的桌面 PowerShell 再运行；不要清除 DPAPI
   文件、放宽 ACL 或全局关闭执行策略。此检查不生成凭据、不点击按钮、不发送消息。

3. 配置私有 HTTPS 并配对（两端均登录你的 Tailscale 网络）：

   先检查 `tailscale serve status`，不要覆盖其他服务。
   Tailscale 的 Windows 配置命令在单独的管理员窗口运行；配对命令回到普通窗口执行。
   Pocket/助手/Codex 不要随之改为管理员运行。详见[上手指南](WINDOWS_QUICKSTART.md)第 3 步。

   ```powershell
   tailscale serve --bg http://127.0.0.1:4317
   tailscale serve status
   ```

   回到普通 PowerShell 的源码目录：

   ```powershell
   py -3 scripts/pair-device.py
   ```

   在电脑显示的页面检查地址，手机扫码确认配对即可。若不能自动发现地址，使用
   `py -3 scripts/pair-device.py --url https://your-pc.your-tailnet.ts.net`。
   已配对的浏览器无需重复扫码。Windows 必须保持清醒、登录且解锁，原生发送不会自动解锁。

This native quick start uses two regular PowerShell windows: initialize/reuse the
credential, start the opt-in Send/Stop helper, then run `serve --native-text-send`.
Run `doctor --native` in the second window before pairing. The checker is read-only,
reports specific setup failures, and never sends a message or modifies permissions.
Native task creation and local-file handoff are disabled in this quick start (see
the separate experimental flags below); native image attachments remain unavailable.
Resume is omitted because
functional acceptance is unresolved. This is still a developer preview, not a
one-click installer or a persistent background service.

## Background mode execution boundary (alternative, not native mode)

- A send starts a **Pocket-owned background turn**, even when Windows is unlocked.
  It does not paste into or click Codex Desktop. The phone says `Windows 在线 · 后台预览`.
- Each background task gets its own private process; readers do not resume tasks.
  Completion releases its writer. Closing/reloading the browser does not close it.
  Closing the Bridge terminal **does** terminate its backends and may interrupt work.
- Stop/approvals work only for tasks owned by this running Bridge. An active task
  owned by Desktop/another client cannot be taken over or stopped by this preview.
- Attachments are disabled in both UI and API, not silently ignored.
- An already-open Desktop conversation may not display external changes until a
  **manual** restart. No automatic Desktop restart or history injection is attempted.
- Use the same native Windows account, Codex installation/version and `CODEX_HOME`
  as Desktop. WSL has a separate filesystem/runtime and is not supported here.
  Keep Codex's configured sandbox and approval policy; the preview does not add
  `danger-full-access`, bypass approvals, or weaken organization policy.

The protocol uses the documented [default stdio transport and thread lifecycle](https://learn.chatgpt.com/docs/app-server).
Desktop's sidebar JSON schema is a local compatibility dependency, not a guaranteed
public API. Projects/pinned/unread parity must be checked on the Windows build.

## Start background mode on a test Windows machine

Requirements: native Windows 11, Python **3.11+**, a signed-in native Codex installation,
Windows PowerShell 5.1, and Tailscale for private HTTPS. Start as your regular user,
**not Administrator**. There is no package download or machine-wide installation step.

From the repository directory in PowerShell:

```powershell
# Replace this example with the actual native CLI binary from your installation.
# It must support `app-server`; do not point at the Desktop GUI exe or a .cmd shim.
$codexBinary = 'C:\path\to\codex.exe'
py -3 windows_bridge.py doctor --codex-binary $codexBinary
py -3 windows_bridge.py init
py -3 windows_bridge.py serve --codex-binary $codexBinary
```

`doctor` only performs the private protocol handshake (no model call, no thread
creation/resume). `init` creates `%LOCALAPPDATA%\CodexPocket` with a protected DACL
allowing the current user and SYSTEM, then stores a DPAPI CurrentUser-encrypted
credential. Existing unexpectedly broad ACLs/reparse points cause refusal instead
of a silent fallback to insecure storage. `serve` binds **only 127.0.0.1**.

On a **new credential initialization only**, `init` marks first-time pairing as
pending. The next successful `serve` (or native phone-preview launcher) opens the
local pairing page once Bridge is ready. If Tailscale Serve is not configured yet,
the page shows instructions; complete them and click “已配置，重新检测”. Existing
installations and ordinary restarts do not reopen the page. Nothing is registered
to run at login. If a browser cannot open in that desktop session, run
`py -3 scripts/pair-device.py` locally; this is also the manual entry point for
adding another device. An explicit `--url` remains supported.

The helper uses `RemoteSigned` for its own PowerShell process only; it does not
change the saved user/machine policy, and managed Group Policy takes precedence.
If PowerShell blocks the local helper, review the repository script and use your
organization's approved script-signing/execution-policy process. Do not globally
disable execution policy or endpoint protection to run this preview.

Keep `serve` running. Perform the Tailscale configuration in a separate Windows
Administrator terminal per its official guidance; run the health check and
pairing command below from the regular user terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:4317/health
```

Tailscale configuration only (Administrator terminal):

```powershell
tailscale serve --bg http://127.0.0.1:4317
tailscale serve status
```

Pairing (regular terminal, source directory):

```powershell
py -3 scripts/pair-device.py --url https://your-pc.your-tailnet.ts.net
```

The computer opens a local QR page with a **five-minute single-use** code,
countdown, regeneration, and a manual-code alternative. Scan on the phone and
click “确认配对” after checking the address. Merely opening a link does not enroll
a device. Regenerating invalidates the previous code. Keep the QR/code private;
the master credential stays in the local Python process. Existing paired browsers
do not need to enroll again. Closing the display does not stop Bridge or Codex;
its server exits after 30 minutes or Ctrl+C. Startup remains manual.

For a text-only alternative, `py -3 windows_bridge.py pair --url https://your-pc.your-tailnet.ts.net`
still prints a link. Both methods require Bridge to be running. After updating
the code, restart Bridge to load the new pairing-status API, not Codex Desktop.

```powershell
py -3 windows_bridge.py devices
py -3 windows_bridge.py revoke DEVICE_ID
```

No public bind, raw app-server socket, firewall opening, sleep-setting change,
automatic sign-in, or startup task is installed. Windows must remain awake;
screen-off, lock, lid-close and sleep are different states. Verify power behavior
on the actual test machine before relying on remote access.

DPAPI uses the [current user's protection context](https://learn.microsoft.com/en-us/dotnet/api/system.security.cryptography.protecteddata).
It is not protection from code already running as that user or an administrator.
Pairing HTTP calls bypass proxy environment variables and refuse redirects so the
master token stays on loopback. Existing per-device token hashing is reused.

## Test checklist

```powershell
py -3 -m unittest discover -s tests -p test_windows_bridge.py
py -3 -m unittest discover -s tests -p test_app_server_lifecycle.py
node --test tests/test_web_capabilities.cjs
```

The native security test uses a temporary data directory and does not touch the
real Pocket credential or Codex login. CI contains Windows-native tests but needs
to run after publication; it is not a substitute for signed-in Desktop testing.

- [ ] DPAPI/ACL test and `doctor` succeed on Windows 11 (including a Chinese username/path).
- [ ] Pair once; refresh/reopen browser; revoked device loses access.
- [ ] Compare Projects, Recents, pinned/unread and usage with the same Desktop account.
- [ ] Create one task in a project and one in Recents; verify both placements.
- [ ] Send a text task, observe streaming/final response, then send another.
- [ ] Start two tasks; stop one; the other keeps running and no writer lock remains after completion.
- [ ] One-shot approval and structured question responses reach the correct task.
- [ ] Attempt to send to a Desktop-owned running task: refuse, no duplicate turn.
- [ ] Refresh/close the browser during a turn: Bridge keeps running.
- [ ] Lock/unlock Windows while it stays awake; record Desktop history synchronization behavior.

Use disposable tasks first. Report versions and sanitized error codes, not tokens,
pairing links, `auth.json`, whole state databases, or private conversation contents.

## What comes next

### One-start desktop diagnostic helper

`windows_desktop_helper.py` is a development-only worker, read-only by default,
with separate opt-ins for fixed smoke-send and experimental native text delivery.
Start it **once in a regular, non-elevated PowerShell
window on the logged-in desktop**. It lasts at most four hours; keep its console
open (minimizing is fine). Closing it or pressing Ctrl+C ends it. No service,
scheduled task, automatic login, or startup entry is installed.

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts\windows-desktop-helper.ps1 -PythonBinary C:\path\to\python.exe
```

The same Windows user can now use SSH to run this launcher with `-Action status`,
`-Action diagnose`, or `-Action stop`. **stop means exit this helper, not stop a
Codex task**. In default mode the worker invokes only the fixed Raw-view diagnostic
script in its own desktop session, with a 25-second child-process timeout. A sleeping host is
still unreachable; this does not unlock Windows or guarantee UIA while locked.

Desktop-control IPC uses an NTFS mailbox under `%LOCALAPPDATA%\CodexPocket\desktop-diagnostics`,
inheriting Pocket's protected current-user/SYSTEM DACL. Credentials reuse the
DPAPI CurrentUser storage; request/response/state files are HMAC-signed. Requests
have an instance nonce, ID, 45-second expiry and a fixed schema with no arbitrary
command or script path. Diagnostic requests have no input fields. The native-text
opt-in accepts only a validated task UUID, expected title and bounded message;
it sends these to a fixed script over stdin, never shell arguments. Worker/client byte locks serialize access,
and heartbeat age distinguishes a live helper from stale files. There is no TCP
listener or additional firewall rule. The directory holds only the latest
sanitized diagnostic/state/response, not a history of screenshots or chat text.
Native requests temporarily contain the message in the protected signed mailbox;
the worker removes each request before processing. HMAC is authentication, not
encryption; same-user code and administrators remain within the trust boundary.
As with DPAPI, this is not isolation from malicious code already running as the
same user or an administrator. No new protection from those principals is claimed.

### Unread-state synchronization

Windows Desktop 26.915 stores unread state in `electron-thread-read-state-v1`,
scoped to the current account identity and execution host. Pocket reads only
that matching local-host bucket; it never merges other accounts or remote hosts.
Opening successfully loaded history sends a narrow `thread-read-state-changed`
notification through Desktop's existing local `codex-ipc` named pipe. It does not
focus a window, send a prompt, resume a task, or write Desktop's state file.
The adapter verifies the pipe server is the same Windows user and belongs to the
installed `OpenAI.Codex` package. Tokens stay in the bridge process; the pipe
receives only the identity/host context, task ID and `hasUnreadTurn=false`.
Desktop validates that context, and Pocket waits for its persisted confirmation
before reporting success. Account changes, stale message revisions, unsupported
schema/host contexts and unavailable pipes fail closed. The native UI helper is
not needed for read acknowledgements, but Desktop must be running.

This is a private, version-sensitive Desktop protocol, not a documented stable
app-server API. The preview currently supports the default local host (no custom
Desktop WebSocket endpoint). Legacy installations keep their previous path.

An optional, explicit cross-session bootstrap is now available:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts\windows-start-desktop-helper.ps1 -PythonBinary C:\path\to\python.exe
```

It uses a temporary, triggerless, current-user
[interactive-token scheduled task](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/ne-taskschd-task_logon_type)
with **Limited** run level while the user is already logged in. It stops only an
existing ready Pocket helper, starts its replacement, and removes the temporary
task registration in `finally`; the four-hour helper remains running. It needs
permission to register a task; if policy denies this, use the manual desktop
launcher. It does not store a password or elevate the desktop worker. A service using
[WTSQueryUserToken](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/nf-wtsapi32-wtsqueryusertoken)
requires LocalSystem and SeTcbPrivilege; no such service is installed. Ordinary SSH subprocess creation by
itself stays in the SSH session; it does not move the process to the desktop.

### Opt-in fixed-draft send smoke test

This is a developer acceptance test, **not a browser/native-send API**. Add
`-EnableSmokeSend` to either helper startup command to allow **one attempt per
helper instance**. Startup alone never sends anything. Then `-Action smoke-send`
on `windows-desktop-helper.ps1` requests that one attempt. It consumes the attempt
even on refusal, error or timeout; an uncertain result must never be retried.

The only accepted draft is exactly `Pocket Windows 测试`. The test script requires
the installed Codex package's window to be foreground in the same unlocked desktop
session, one visible editable field with that exact value, and one semantically
identified Send button within its immediate composer group. It rechecks live
state and calls UIA Invoke once. It never writes a draft, navigates, focuses,
presses keys, or exports the draft value. The fixed string is a test constraint,
not a substitute for exact task identity in a future general-purpose adapter.

`invoke_requested` means only that the
[UIA request was made](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.invokepattern.invoke).
Check the intended Desktop history for receipt and completion separately. No
automatic retry, Stop, arbitrary prompt, attachment, or cross-task action is exposed.

### Experimental ordinary-text delivery (initial real-machine acceptance passed)

Receipt confirmation now makes up to 32 initial history checks (250 ms between
checks; history RPC time is additional). This covers the observed Windows case
where the user message was persisted just after the previous ~3-second window.
When the server advertises `nativeReceiptPolling`, the phone follows a timeout
or uncertain response with up to eight `receiptOnly` checks, with 1–5 second
backoff and a 90-second overall browser deadline. These checks verify the same
request ID/content and its original receipt baseline; they cannot claim a missing
request, allocate a task, or invoke Desktop Send. Older servers and Resume do
not use this polling path. Confirmed late receipts clear only the submitted
task's draft and refresh actual history, without pretending a completed turn is
still running. Exhausted checks preserve the draft/request ID; they do not mark
the delivery failed, clear uncertain journals, or start another turn.

2026-09-20 real Windows acceptance used the disposable Pocket test task and a
temporary Bridge that hid new history for four seconds (read-layer simulation
only; no rollout edits). The first confirmation returned uncertain; receipt-only
polling then confirmed it, repeated polling returned the same receipt, and an
unknown request stayed unclaimed. Desktop dispatch count and new turn count were
both exactly one, with final reply `POCKET_RECEIPT_OK`. The temporary paired test
device was revoked. This validates the server path, not a new manual phone test.

This is a separate opt-in from the fixed smoke test:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts\windows-start-desktop-helper.ps1 -PythonBinary C:\path\to\python.exe -EnableNativeSend
py -3 windows_bridge.py serve --codex-binary C:\path\to\codex.exe --native-text-send
```

For a time-limited phone test that survives closing the SSH connection, start the
desktop helper above, then use the launcher below **instead of** foreground `serve`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts\windows-start-phone-preview.ps1 -PythonBinary C:\path\to\python.exe -CodexBinary C:\path\to\codex.exe -Minutes 120
```

It uses a temporary Interactive/Limited scheduled job, removes its registration
after startup, refuses an occupied port, and closes the bridge after at most two
hours. It installs no auto-start service and does not restart Desktop. Existing
Tailscale Serve configuration is independent and can be disabled with
`tailscale serve --https=443 off`; the Mac's separate endpoint is unaffected.
Pair via the private Tailscale HTTPS link as described above. The phone browser
must keep Tailscale connected and Windows awake/unlocked for native sending.

- By default only existing-task ordinary text is routed to the native helper.
  Task creation and local-file handoff require the separate experimental flags
  below; native image attachments are unsupported. Native Resume is a separate experimental
  opt-in with the known limitation below. Native Stop
  requires the separate opt-in below.
  There is no fallback to app-server execution on native failure.
- Task identity uses the current `thread.name` from read-only app-server calls,
  complete bounded title-uniqueness checks, a fixed `codex://threads/<UUID>` link,
  and the live UIA document title before writing and invoking Send. The tested
  client's SQLite `threads.title` can still be the initial prompt after renaming;
  it is not the source of truth. Titles are a compatibility dependency, not an
  immutable UI-exposed task ID; duplicate/unknown titles fail closed.
- Requires an awake, unlocked active local console and a uniquely identified
  visible Codex window/editor. Existing nonempty drafts are not replaced, even
  if they appear to match the requested message. No clipboard or keyboard fallback.
  Navigation leaves the source composer untouched; only the verified destination
  composer is checked for a draft. An unrelated paused task's placeholder must
  not prevent sending to an idle target task.
  The observed empty UIA value `\n随心输入` is accepted only with a nearby enabled
  voice-start action and no enabled Send/Stop/Resume/Queue action. The same text
  with Send available remains a draft. The adapter may request foreground
  activation of the uniquely verified Codex window; refusal prevents writing.
- `native-delivery.sqlite` under the protected Pocket state directory stores only
  IDs, hashes, status and history receipt IDs, not message text. It commits a claim
  **before** asking the helper to write or invoke. A process crash/timeout leaves
  the request uncertain; neither the same ID nor a fresh ID on that task can
  automatically issue another send until the original is reconciled.
- The browser retains the request ID and message digest in session storage. A
  retry with the same ID only reads history. Confirmation requires a new stable
  turn/item ID in the exact target task with exact submitted text (allowing the
  single trailing LF and literal-underscore escaping added by the tested Desktop
  serializer). Other formatting transformations still fail closed. A click result is
  not confirmation. No broad substring match or old identical message is accepted.

`scripts/windows-native-acceptance.py --thread-id ... --codex-binary ... --confirm-send`
is an explicit real-model test: it starts an ephemeral loopback Bridge, submits a
fixed harmless acknowledgement prompt, verifies the receipt, and replays the same
request ID to test idempotency only after success. It closes the test Bridge on exit.
It is **not** part of CI and must not run against an unrelated task.

Real-machine acceptance on Desktop 26.915.3509.0: empty-composer checks, fixed
protocol navigation, native ValuePattern text insertion, Invoke, and exact-task
history receipt all passed. Desktop returned `POCKET_WINDOWS_NATIVE_OK`.
An initial receipt was reconciled without another UI action after accounting for
Desktop's underscore escaping; a second fresh request returned HTTP 202 directly.
Replaying each confirmed request returned `duplicateRequest: true` without sending
again. The initial empty-editor refusal was traced to UIA exposing the Chinese
placeholder as content, not to a user draft. Bidirectional switching between two
user-designated test tasks also passed native sends and receipt replay, with the
helper reporting a different source task before each navigation. Ten browser
tests cover capabilities, unsupported paused-task resume, request IDs, and late
responses preserving the newly selected task/draft. The private HTTPS page and
bridge health were verified from the Mac. The user subsequently confirmed phone
pairing and sending in the designated test task. Wider localization/formatting
cases still need acceptance. This remains opt-in.

### Guarded native Stop (experimental opt-in)

Add `-EnableNativeStop` alongside `-EnableNativeSend` when starting the desktop
helper. Use the same `--native-text-send` Bridge mode. A fresh authenticated helper
heartbeat advertises `nativeStop`; only then can the browser enable its Stop
button for a task with a known running turn ID. The helper's plain `stop` command
still exits only the helper, not a Codex task.

Stop first navigates to the UUID and prepares a unique semantic Stop button in
the verified foreground composer. It does not change the draft. Bridge then
re-reads the exact turn after navigation, checks the current title, and commits
only against the same document/button UIA runtime IDs with an eight-second guard.
The commit never navigates or activates a window. Locked/unavailable desktops,
duplicate titles, changed turns, lost focus, changed buttons and stale guards
fail closed. No coordinates, Enter key, background interrupt or Desktop restart
is used. This is still a UI compatibility mechanism, not an atomic Desktop
turn-specific API: users should not concurrently start another turn during Stop.

The per-turn durable `native-stop.sqlite` claim prevents re-clicking after an
uncertain response or process restart. Repeated requests reconcile history only.
An uncertain pending claim conservatively blocks a new Stop on the same task
until its target turn is reconciled. Use actual persisted `task_started`,
`task_complete` and `turn_aborted` events: a separate reader can report a currently
running Desktop turn as `interrupted`/`notLoaded`, which is not proof of a stop.

On 2026-09-18, a disposable native task in “测试 Pocket Windows” was sent, stopped
and verified via its persisted interruption event. Replaying Stop returned
already-finished without another click. An earlier test was left running after
preflight refused and later completed naturally; it was not counted as Stop
success. The user subsequently verified phone sending and Stop on the same
Windows machine. Wider localization/locked-state acceptance remains open.
`scripts/windows-stop-acceptance.py --thread-id ... --codex-binary ... --confirm-send-stop`
is an explicit real-model test for disposable tasks, not CI.

### Guarded native Continue (experimental opt-in)

Add `-EnableNativeResume` when starting the desktop helper, alongside
`-EnableNativeSend -EnableNativeStop`. The browser shows Continue only when the
fresh helper heartbeat advertises `nativeResume`, the selected turn is paused,
and its turn ID is known. Keep the phone composer empty; sending a new prompt
directly into a paused task is not supported by this preview yet.

Continue uses the actual Desktop Resume/Continue button, never a synthetic
message or app-server resume call. Prepare navigates and checks a unique button
and empty destination composer; Bridge then rechecks the same interrupted turn
before committing against the short-lived UIA document/button guard. Real drafts,
changed tasks, stale guards and missing/ambiguous buttons refuse the operation.
The same UI race limitation as Stop applies: do not concurrently start work or
edit the Desktop composer during this operation.

`native-resume.sqlite` records request IDs and the expected paused turn. A lost
response or restart never causes another click for the same request; pending
uncertain requests block a new resume until their result is reconciled. Only
actual persisted running/completed lifecycle state confirms delivery. This is
not proof that the interrupted work meaningfully continued.

On 2026-09-18, “确认收到” triggered a new turn through its native Desktop button.
Replaying the request returned its receipt without another click. However, both
the original test and a fresh Stop/Continue round trip completed with an empty
final reply (gpt-5.6-terra, low). Functional resume acceptance has **not passed**;
the same empty completion was reproduced by a user manually clicking Desktop
Resume (about 1.5 seconds, no subsequent tool execution). This is not unique to
the automated click; the underlying cause and generality across tasks/models
remain unconfirmed. Do not present the delivery receipt as
successful recovery of the original work.
After that empty turn completed, sending an explicit follow-up asking to finish
the pending test did execute a 10-second wait and return the expected final
marker. This was a new user message, not native Resume, and does not establish
support for sending directly into a still-paused task. Pocket does not silently
inject such a follow-up or retry on the user's behalf.
`scripts/windows-resume-acceptance.py --thread-id ... --codex-binary ... --confirm-resume`
is an explicit real-model test for a user-designated paused disposable task. It
now waits for a nonempty final reply and fails on an empty completion; add
`--expect-final-text YOUR_TEST_MARKER` to verify a specific expected result.
Timeouts fail acceptance without another click or automatic retry.

### Experimental native new tasks (Project multi-file read and Recents file-read checked)

新建任务的代码与模拟测试已接入，**默认关闭**。2026-09-20 已验证一次请求
完成 Recents 任务创建、Desktop 首条消息发送和文本附件读取；同一请求重放
没有重复创建或发送。读取工具返回了完整文件口令，但模型最终回复漏了末尾
字符，因此严格的最终回复匹配测试仍记为失败，另以工具输出核实读取成功。
Project 纯文本创建、归属、首条 Desktop 原生发送和重复请求已在 2026-09-20 实机通过；
Project 带两个附件创建、读取和重复回执检查也已通过（见下方实机记录）；
随后用户也已从手机在 Pocket 下创建带 JPEG 图片的新任务，Desktop 图片读取与最终回复已核实；
手机端最终显示效果仍需用户确认。基础预览仍只控制已有任务；
不要将本节描述当作已经验证的正式支持。

```powershell
py -3 windows_bridge.py serve --codex-binary C:\path\to\codex.exe --native-text-send --native-new-tasks
```

For the bounded phone-preview launcher, the equivalent opt-in is
`-EnableNativeNewTasks`. The same native Send helper is required. No additional
helper UI action, coordinate click, keyboard shortcut, or background model turn
is introduced.

- The plus beside a Project carries that saved project ID. Recents passes an
  explicit null project and receives its own workspace under `Documents/Codex`.
  Raw caller-supplied working directories are not used.
- Modern Windows Desktop projects are read in a single read-only SQLite snapshot
  (projects, roots and explicit thread assignments). Modern IDs replace legacy
  JSON IDs; unassigned tasks never get regrouped by their working directory.
  Creation passes `projectId` to the installed app-server's experimental
  `thread/start` API, then verifies persisted membership before Desktop Send.
  No Codex database writes or legacy JSON assignment edits are made on this path.
  The installed CLI schema was checked using `generate-json-schema --experimental`,
  following the [official app-server schema workflow](https://developers.openai.com/codex/app-server/).
  Read failures fail closed; older databases without a projects table retain
  the legacy path. New protocol versions still require compatibility validation.
  Real-machine acceptance in the disposable `Pocket` project returned
  `POCKET_PROJECT_OK`; same-ID replay retained one task and one user message.
- 2026-09-20 live Windows Bridge multi-file acceptance uploaded a UTF-8 Chinese/
  space-named `.txt` and a space-named `.json`, then created exactly one task in
  `Pocket`. Both workspace copies had identical SHA-256 content hashes to their
  uploads, and the Desktop model used a read-only command to return both random
  file-only codes exactly in its final JSON reply. There was one turn and one
  user message; receipt-only replay returned the same task and did not resend.
  The temporary paired device was revoked. Test copies remain in the project
  `.codex-pocket-attachments` directory. This used the production HTTP routes
  over loopback, not an Android/iOS browser or image-understanding test; those
  end-to-end manual checks remain separate.
- Subsequent user-initiated phone acceptance on the same Windows build created
  a task under `Pocket` with a JPEG attachment. The native delivery journal
  confirmed receipt, Desktop invoked `view_image` on the workspace copy and
  received an image result, then completed a nonempty image-description reply.
  The rollout contains exactly one started/completed turn. This verifies the
  phone-initiated upload/create/native-send/model-read path; final phone-side
  rendering was not directly inspected. No native image-chip support is implied.
- A short-lived app-server writer creates/names/materializes **only an empty
  thread**, then exits. Sidebar metadata is assigned and reread; the returned
  thread ID, working directory and collection must match before native delivery.
  The first model turn is sent through Desktop, never `turn/start` in app-server.
- Existing tasks can keep running. Only the new destination's identity and empty
  composer are checked before sending; existing drafts are not overwritten.
  A repeated title gets a numeric suffix to support unique native navigation.
- Browser request IDs survive refresh. `native-create.sqlite` commits the claim
  before allocation and retains the known ID before assignment/delivery. Replayed
  requests reuse the same task and native delivery receipt, never recreate it.
  An allocation timeout without a known ID blocks subsequent allocations rather
  than guessing. This preview does not auto-clear such uncertain records; they
  require operator investigation. Do not delete credentials/journals to retry.
- If an empty task exists but delivery fails, the phone retains its draft and
  opens the known task when available. An uncertain send carries the same delivery
  ID into that draft's retry so only receipt reconciliation occurs. A failed
  collection check sends nothing and requires checking the assignment first.
- Explicit Recents metadata overrides directory-based grouping. A replay refuses
  a changed working directory/collection rather than moving a user's task back.
- Attachments remain disabled unless the separate local-path option below is enabled. Locked desktops, task appearance latency, model
  selection and broader phone-side Project/Recents display checks still need
  acceptance before enabling this by default.

### Experimental uploads as local file paths (existing-task text-file read checked)

This is **not native image attachment support**: Desktop receives an ordinary
text message containing local paths, and the task must read those files with its
available tools. Image interpretation depends on the selected model/tools and
file access permissions; a delivery receipt does not prove the file was read.
There are no native image chips, clipboard writes, file-picker automation or
app-server `turn/start` calls. History displays the question and paths.

Enable explicitly alongside native sending:

```powershell
py -3 windows_bridge.py serve --codex-binary C:\path\to\codex.exe --native-text-send --attachment-paths
```

Add `--native-new-tasks` to use this with new tasks too. The bounded phone launcher
equivalent is `-EnableAttachmentPaths` (optionally with `-EnableNativeNewTasks`).
The same native Send helper is used, without additional UI permissions.

- Up to four files, 20 MB each. Upload/resolve requires the same paired device;
  clients submit opaque upload IDs, never arbitrary filesystem paths.
- Windows device names, alternate data streams, traversal, metadata filename
  collisions, symlinks and reparse-point ancestors are refused.
- Before delivery, Pocket retains content-hashed copies in
  `%LOCALAPPDATA%\CodexPocket\attachment-handoffs` under the protected data-folder
  ACL, then copies only those verified uploads into the destination task's
  `.codex-pocket-attachments` workspace subdirectory. The latter inherits the
  existing workspace permissions; Pocket never changes ACLs or sandbox policy.
  The cwd is read from the verified target task, never from an HTTP-supplied path.
  New tasks resolve and verify their final workspace before making these copies.
  Modified copies and symlink/reparse destinations are refused, not overwritten.
  Both sets of copies **do not expire automatically**, including on a failed send.
  Removing an upload from the phone or its one-hour expiry does not delete a
  retained copy or break a submitted path. Once no task needs them, you may remove
  those specific files manually; the preview has no cleanup UI yet. Uploads and
  retained copies consume disk space, so keep this experiment scoped. Workspace
  copies can appear as untracked Git files; Pocket does not edit `.gitignore`,
  stage or commit them. Review/ignore them before publishing your repository.
- Send retries keep the same ID and include file identity. Unknown native sends
  only reconcile receipts; they never automatically click Send again. Failed
  requests keep the phone draft and attachments. Expired/deleted uploads must be
  reselected; first check Desktop history if the original send was uncertain.
- New-task failure handling preserves the known task and attachment draft,
  rather than creating a second task. Recents/Project assignment guards still apply.
- No deployment or real model/image-reading acceptance was performed while
  implementing this flag. Validate on disposable tasks before broader use.

2026-09-18 unlocked real-machine check: an isolated loopback Bridge accepted a
UTF-8 text upload with a Chinese/spaced filename (HTTP 201) and sent its retained
path through Desktop to the disposable task. This initially returned
`native_delivery_uncertain`: Desktop 26.915 serialized embedded line breaks as
Markdown hard breaks as well as escaping underscores. Receipt matching now
constructs that specific whole-message representation (no generic unescaping,
substring matching or whitespace trimming). The original durable request was
then reconciled against its actual turn/item receipt; replay returned
`duplicateRequest` without a second native invocation.

**The model could not read the file:** its file-read command returned Access
Denied, and its final answer was `FILE_READ_UNAVAILABLE`. The protected Pocket
directory is not automatically readable by the Windows sandbox. This initial
file-read acceptance failed. Do not grant sandbox access
to the Pocket data root, which also holds pairing credentials. The attachment
storage was subsequently changed to verified workspace copies as described above.
[Official Windows sandbox documentation](https://learn.chatgpt.com/docs/windows/windows-sandbox#grant-sandbox-read-access)
describes explicit read-directory grants; Pocket does not issue them or change
sandbox/ACL policy automatically. The isolated test device was revoked, the
temporary HTTP listener shut down, and the live phone service was not replaced.

After the user chose workspace storage, the same isolated HTTP-upload/native-send
test passed with a new random token on the unlocked Windows machine: upload 201,
delivery 202 confirmed by thread history, replay 202 with `duplicateRequest`,
exactly one native invocation, and a completed model file-read command followed
by the exact token from the file (the token was not included in the prompt).
This establishes **text-file reading in an existing task**, not image interpretation,
phone-network upload or new-task file-reading acceptance. The new-task path has
automated coverage for copying after verified cwd selection, replay without
reallocation, and preserving a created task if copying fails. It remains opt-in;
the running phone service has not yet been replaced by this staging build.

2026-09-20 new-task acceptance created exactly one disposable Recents task:
`Pocket Windows file task acceptance 0920`. It exposed three compatibility cases:

- Desktop 26.915 omits persisted tasks without user events from `thread/list`,
  including scan-and-repair mode. The generated local protocol schema has no
  include-empty option. After a complete API collision scan, the adapter may
  now supplement a missing target with read-only metadata from the newest
  `state_N.sqlite`: exact ID/name, non-archived empty-task flag, and no other
  matching active name/title/preview. Missing/unknown schemas fail closed.
  This supplements the documented [list filters](https://developers.openai.com/codex/app-server/#list-threads-with-pagination--filters),
  not an undocumented request parameter or database write.
- An unready source-page composer could prevent navigation. Text sending may
  now navigate after that specific source-editor ambiguity, with a bounded
  destination readiness wait. Destination identity, unique writable editor,
  draft, foreground and button checks remain mandatory. Lock/window/provider
  failures and Stop/Resume source checks are not relaxed.
- Desktop can replace legacy JSON projectless markers. For an already-known
  Recents allocation, replay may verify the modern database's null `project_id`,
  non-archived state and exact Windows cwd instead. Unknown metadata or a moved
  task still refuses; replay never rewrites collection metadata.

Initial attempts were refused before draft writes. Explicit new delivery IDs
then retried only the known created task, never rearmed a refused/uncertain
journal entry or allocated another task. Its first user turn completed a real
file-read tool call and returned the exact random token from the upload. That
successful send replayed with `duplicateRequest` and one native invocation.
Replaying the original refused creation preserved the same thread ID and issued
no allocation or UI call. This verifies creation plus recovery/file reading,
**not yet a clean first-pass create-with-file request after all fixes**, project
creation, image interpretation, or phone-network acceptance. The temporary test
device was revoked, the isolated listener closed, and the live service remained
on its prior version.

A separate fresh request on 2026-09-20 then created
`Pocket Windows one-pass file acceptance 0920` and completed the native send
without a recovery request: upload 201, delivery 202 confirmed by history,
Recents membership verified, and replay 202 with the same task ID and exactly
one UI invocation. The model's file-reading command exited 0 and returned the
exact random second-line token; a read-only follow-up also verified the workspace
file's content hash. Its final reply omitted the last four token characters, so
the original strict final-answer acceptance result remains failed rather than
being overwritten. Independent tool-output evidence establishes successful
file delivery and reading, not exact model instruction compliance. The temporary
test device was revoked and the isolated listener closed. This exercised the
loopback HTTP upload/create routes and native UI, not the phone network or the
live helper mailbox. Project-specific creation and image interpretation remain
unverified.

After those checks, the test machine's phone Bridge and Desktop helper were
switched to a separate versioned preview directory using the existing bounded
launchers (120-minute Bridge session). Native Send, Stop, new tasks and local-path
uploads were enabled; Resume and display-off experiments remained disabled.
The existing pairing/state directory and previous source version were retained;
Codex Desktop was not restarted. The Tailscale HTTPS health endpoint advertised
`newTasks: true`, `attachments: true`, and `attachmentMode: localPaths` after the
switch. This confirms deployment readiness, not a phone-originated file-upload
acceptance; that remains the next manual check.

The post-deployment doctor check exposed a diagnostic-only false failure after
pairing: the master credential intentionally no longer authorizes Desktop control
routes. Doctor now authenticates against the read-only `/api/devices` admin route
and separately reads `/health` capabilities, without printing device metadata,
enrolling a diagnostic device, or relaxing control authorization. Regression
tests cover migrated pairing and refusal when admin authentication is unavailable.

Native text sending now prepares the window before the first composer scan on an
unlocked desktop: find one verified Codex package window, restore it only if
minimized, request foreground activation, and wait briefly for the observed state.
The destination title, empty draft, foreground and live controls must still pass
before writing or invoking Send. A failed activation request is not treated as
success; normal task navigation may activate the app, but the final foreground
guard remains mandatory. Multiple windows, hidden non-minimized windows, locked
sessions and restore failures refuse safely. Stop/Resume commit and the separate
display-off experiment do not use the new early activation step. No synthetic
keyboard input, always-on-top setting, focus-policy changes or Desktop restart
is used. This follows the bounded asynchronous semantics of
[ShowWindowAsync](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindowasync)
and the OS restrictions on
[SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow).

On 2026-09-20, two disposable-task checks began with a temporary empty test window
in the foreground. Both the covered-Codex case and the minimized-Codex case
activated before scanning, sent one fixed instruction, and completed the expected
reply. The minimized case also confirmed `restoredWindow`. Replays did not invoke
UI again. The first test harness initially read the nested replay flag incorrectly;
a separate replay-only check verified the existing receipt without resending.
These tests used the staging sender; phone-service deployment is checked separately.

**Coverage correction (2026-09-20):** that original foreground fixture itself
launched the sender process. Windows can give a process started by the foreground
process additional activation eligibility, so those results did **not** prove
that a resident helper can activate Codex over an unrelated app. A subsequent
phone request passed all three scans but reported `activatedBeforeScan=false`
and `foreground_task_changed`; it refused before writing or sending.

The adapter now records the actual `SetForegroundWindow` return and observed
foreground outcome. An intermediate UI Automation `SetFocus` fallback failed:
the top-level provider was not focusable, and focusing the verified composer did
not bring Codex above Windows Terminal after the user clicked Terminal. That
request was refused before writing. The ineffective UIA fallback was removed;
[SetFocus need not bring a control to the foreground](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.automationelement.setfocus).

The framework's
[Interaction.AppActivate(Int32)](https://learn.microsoft.com/en-us/dotnet/api/microsoft.visualbasic.interaction.appactivate)
was also tried with a verified numeric PID, not fuzzy title matching. A separate
no-write/no-send probe activated the covered window, but the subsequent **actual
phone request after a fresh manual click on Windows Terminal failed**: both
AppActivate attempts returned without error yet neither acquired the foreground.
That message was refused before writing. This fallback was therefore removed too.
The current build retains bounded activation telemetry, not a claim that the
covered-window bug is fixed. No synthetic input, topmost flags, foreground-lock
settings, elevated processes or Desktop restart were introduced.

A read-only taskbar scan found an exact package-AUMID button on Windows 11, but
it exposed only ScrollItemPattern, not InvokePattern or SelectionItemPattern.
A real taskbar click was therefore implemented as a separate local opt-in after
explicit user approval. Add `-EnableTaskbarActivation` alongside
`-EnableNativeSend` when starting `scripts/windows-start-desktop-helper.ps1`
(or use the equivalent `--enable-taskbar-activation` Python helper flag).
It defaults off, cannot be enabled by a phone payload, and requires native send.
Restart the Pocket helper without this flag to disable it; no Codex restart is
needed. Stop/Resume and display-off never use it.

Only after normal destination activation fails, the sender may click **once** on
the exact taskbar app ID derived from the installed package and actual window
executable. Packages can include command-runner entrypoints, which are excluded.
It verifies the Explorer taskbar process, visible unique button, live rectangle,
physical window hit and UIA identity. For the observed Windows 11 provider that
returns only Shell_TrayWnd from a point hit, it additionally requires exactly one
live app-button rectangle containing that point and matching the selected button.
No fuzzy title matching, fixed coordinates, right-click, or thumbnail selection.
Ambiguous, obscured, hidden/autohide or changed controls refuse. The cursor must
be stationary and keys/buttons released; native checks run immediately before
the down/up pair. No click retry occurs. The pointer is restored only if still
at the injected point, keys are released, and the validated desktop remains safe.
This necessarily uses physical input: simultaneous local interaction can still
race the last check, so users sharing the desktop should leave the opt-in off.

After the click the exact Codex HWND must be foreground, then controls are
rescanned and all original destination, draft and Send-button guards apply.
Composer typing/submission still uses ValuePattern/InvokePattern, not mouse or
keyboard simulation. Mouse-input layout was compiled and checked on Windows;
identity tests perform no clicks. A no-write probe confirmed one taskbar click
foregrounded Codex. Most importantly, the user then clicked another application
and sent from the paired phone through the existing Bridge and resident helper:
both normal activation attempts were denied, the taskbar fallback reported
`clicks=1` / `foreground_confirmed`, and delivery was confirmed in thread history.
No Desktop restart, pairing reset, elevated process or foreground-policy change
was required. This passes the reproduced covered-window scenario, not all
Windows layouts or concurrent-input cases.

Independent-cover tests through the **existing live HTTP Bridge and resident
helper** passed both cross-task and same-task sends to the disposable task
`确认收到`, with history receipts, completed replies and idempotent replay.
The covering process did not launch the sender. Temporary test device credentials
were revoked afterward; Desktop/helper were not restarted. However, the logs show
task-URI navigation activated Codex in these runs, so they are **not evidence of
the activation fallback succeeding**. The subsequent manual-input failure and
failed AppActivate follow-up are described above.

Deployment of this foreground change was then blocked **before stopping either
live service** by the existing credential-directory ACL check. Read-only
inspection found extra explicit Owner Rights (`S-1-3-4`) and Administrators
(`S-1-5-32-544`) allow entries on `uploads` and `attachment-handoffs`, whose
creation paths use `mkdir(mode=0o700)`. The check requires only the current user
and SYSTEM. This is an attachment-directory compatibility issue, not evidence of
bad pairing or a foreground-send failure. No ACL, credential, pairing, upload or
live source was changed to bypass it. The foreground fix remains staged until
the attachment directory creation/inheritance and existing-directory repair are
handled explicitly.

With explicit approval, this was subsequently repaired on the test machine.
Windows attachment directories now inherit the validated private Pocket parent
DACL; they no longer request Python 3.13's special `0700` DACL. The shared Mac
attachment store retains its existing POSIX modes. New upload subdirectories and
retained snapshots follow the Windows inheritance rule too.

For an existing affected installation, `scripts/windows-repair-attachment-acl.ps1`
previews by default. Its explicit `-Apply` mode requires the Bridge listener to be
stopped, validates the private parent and the complete bounded attachment trees,
backs up ACL metadata under the private Pocket directory, and changes only the
DACL of `uploads`, `attachment-handoffs`, and their existing descendants. Unknown
owners/principals, deny entries and reparse paths fail closed. Credentials,
workspace permissions, owners, audit settings and unrelated data are not repair
targets. No administrator rights or relaxed credential checks are required.
The initial generic ACL setter requested an unnecessary security privilege in the
limited desktop session; the final implementation persists the Access section
only through the .NET file/directory API and does not write owner or audit sections.

The real repair covered six entries and verified that the parent ACL and encrypted
credential were unchanged. Both the Bridge and helper then started from the
versioned foreground/ACL build; the Tailscale HTTPS health check succeeded. Existing
pairing was retained, and Codex Desktop was not restarted. The original ACL backups
remain local for inspection. Tests include an isolated Windows ACL fixture (no real
credentials), rejection with a live listener or an unknown principal, unchanged
attachment contents, and Windows upload/handoff regression coverage. CI now includes
Python 3.11 and 3.13 to cover the platform-specific mode behavior.

A later phone send restored and foregrounded Codex successfully but refused with
`incomplete_scan` before writing. The sender now records bounded, content-free
scan telemetry (stage, elapsed milliseconds, visited/queued counts and a classified
reason) instead of conflating time, node and depth limits. Normal unlocked text
delivery can retry read-only snapshots up to three times per readiness check,
sharing a 24-second pre-write deadline. Only time limits, unavailable UI elements,
temporary editor ambiguity and a not-yet-matching destination are retryable.
Node/depth limits, lock/window failures and draft conflicts remain failures.
Stop/Resume and display-off flows do not gain this retry loop. After `SetValue`
begins, no snapshot failure can cause an automatic write or Send retry.

Windows PowerShell 5.1 regression tests also cover explicit floating-point scan
budgets: the initial test build selected an integer overload and overflowed on the
default post-write deadline. That build was not deployed; its exact disposable
test draft was cleared without submitting it, and the uncertain journal entry was
not rearmed. The corrected build passed a fresh minimized/cross-task send to
`确认收到`, completed the fixed expected reply, and replayed with one native
invocation. Source, destination and post-write scans were recorded separately.
Synthetic readiness tests cover transient recovery, persistent failure, expired
deadlines and non-retryable safety failures; the real successful run did not need
a transient scan retry.

2026-09-18 locked-screen follow-up on the Windows 11 test machine:
17 file-handoff tests and 18 mocked-UI creation tests passed on Windows; PowerShell
launcher parsing also passed. These are host-native automated tests, **not** a
model-reading-file acceptance. They ran in a separate staging directory without
replacing the live phone Bridge.

The initial guarded send probe refused with `foreground_task_changed`, despite
the screen being locked: checking the input desktop name alone was insufficient.
Native Send/Stop/Resume and smoke-send now additionally require an active console
session whose [WTS session flags](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoex_level1_w)
explicitly report unlocked; unknown queries fail closed. The shared read-only
guard in `scripts/windows-session-state.cs` has 11 policy/layout checks that
passed on the machine. A second interactive-session probe was refused with
`desktop_locked_or_unavailable` before navigation or draft writes. An intentionally
impossible expected title additionally prevented sending if the user unlocked
mid-probe. Unlock-to-send behavior and actual file reading still require acceptance;
this staged guard has not yet replaced the running helper.

Display-off follow-up the same day: the isolated observer received Windows
`GUID_SESSION_DISPLAY_STATUS = 0` and SSH remained reachable. No messages were
submitted: the first attempt had no exposed editor until explicit navigation;
the next attempt was refused by the foreground identity check. A display-on
control briefly reported 1 but returned to 0 before delivery, so it was not a
valid sustained-display-on comparison. A separate read-only probe identified
the foreground process as `LockApp`, not Codex, in `WinSta0` session 1.
Consequently **pure display-off native delivery is still unverified**, not
declared supported or impossible. WTS/unlocked plus an accessible input desktop
alone is not proof that Codex can receive input; keep the foreground check.
The user must return to the ordinary desktop before repeating this comparison.
The existing native-text phone Bridge was restarted for a bounded 120-minute
preview after reboot; no security/power policy or permanent autostart was changed
by this test.

After the user explicitly reopened Codex, a further display-off attempt verified
that the foreground PID/handle matched Codex before turning off the display.
The observer recorded display state 1 -> 0; it stayed 0 throughout the native
send attempt. Sending was refused with `foreground_task_changed`, with no draft
write or message submission. A subsequent probe returned foreground handle/PID
0 (not `LockApp` this time), while the phone Bridge remained healthy. Thus the
current foreground-dependent adapter does **not pass display-off acceptance on
this machine**. This is not proof that all Windows UI Automation while screen-off
is impossible; a separately guarded screen-off adapter would require its own
design and acceptance, without removing the current safety checks.

A separate **manual, default-off experiment** now exists via
`windows-desktop-send.ps1 -AllowDisplayOff` (or the local Python helper argument
`allow_display_off=True`). It is not enabled by the phone launcher or mailbox;
remote request fields cannot enable it. It only applies to text Send, not
Stop/Resume. A missing foreground window is accepted only with an observed
display-off notification, an unlocked active console session, no same-session
`LogonUI`, and unchanged window/document/editor runtime identities. Title, draft,
editor and receipt guards remain in force. Unknown state refuses the operation.

The observer compiled on the Windows test host and all 11 display-policy checks
passed. **Real display-off acceptance still failed before writing any draft.**
The follow-up read-only probe found display state 0, foreground handle 0 and WTS
unlocked, but also a same-session `LogonUI` process. These conflicting signals
are not treated as permission to send; the experiment reports
`display_off_security_state_unconfirmed`. The live phone Bridge/helper were not
replaced. Confirm whether waking the display requires sign-in before further
acceptance; do not remove this guard or change lock policy to force a pass.

### Standalone probe

Use `scripts/windows-desktop-diagnostic.ps1` from a **regular PowerShell window
in the signed-in Windows desktop** to inspect Codex's UI Automation tree. The
probe does not click, type, capture screenshots, or export input/conversation text.
It reads editor values only to report length/category/placeholder metadata.
It outputs control types/patterns and only allowlisted
Send/Stop/Resume and fixed voice/dictation/queue/steer labels. Arbitrary names and
AutomationIds are omitted. Schema 4 adds exact button Name/HelpText hints and
presence flags; `windows_desktop_controls.py` classifies only buttons in the
unique editable field's immediate group. It never authorizes an action.
The default is the detailed Raw view; `-View Control` reproduces the filtered
view. Framework/window-class hints are also strictly allowlisted. A successful
scan with only a web document reports `web_content_found_no_composer`; any
truncation/provider error reports `incomplete_scan`. Window-caption Restore is
excluded by requiring action candidates to be inside the web document. Neither
case establishes send capability. `candidate_controls_found` still needs manual
verification of the exact composer and task before implementing actions.
SSH runs in session 0 on the tested machine; the probe reports
`interactive_session_required` there instead of pretending the GUI is available.
Keep Codex open and unlocked; UIA inspection can be incomplete if a provider is
unresponsive. The probe limits traversal to Codex windows, 1,200 nodes and depth
64, with a cooperative 15-second budget (an individual provider call can block).
The report names the depth/node/time limits actually reached. Use
`-OutputPath C:\path\to\desktop-diagnostic.json` to write UTF-8 directly; piping
the child process output through Windows PowerShell 5.1 may corrupt Chinese labels
when the parent console code page differs. No input text is read: `valueReadOnly`
and `keyboardFocusable` record capability metadata only.

After inspecting the interactive report, add
a separate native adapter with exact thread identity, draft preservation, and
unique semantic Send/Stop checks. Never substitute blind coordinates or an
unverified Enter key. Only after that adapter passes tests should the default
Windows mode become Desktop-owned like the Mac version.

The older `start-mobile-codex-stack.ps1` / `mobile_codex_control.py` files belong
to the legacy upstream stack; **they do not start this Pocket preview**.

## Real-machine validation (2026-09-18)

Windows 11 build 26200.9457, Desktop 26.915.3509.0, native CLI
0.155.0-alpha.9, Python 3.13.15 (portable, test-directory only):

- Windows-native DPAPI CurrentUser roundtrip, protected ACLs, Unicode/space data
  paths, and corrupt-credential refusal tested with disposable data.
- Windows API contracts and browser capability tests passed; fake backends are
  used for send/stop/approval behavior, so these are not model execution evidence.
- The real native app-server passed isolated creation/settings/reader/writer
  release tests without account credentials or model inference.
- A real isolated Bridge passed DPAPI-backed startup, authenticated loopback
  capability status, single-use pairing-ticket creation, and clean shutdown.
- Fixed explicit SQLite `uri=True` for portable read-only model metadata access,
  UTF-8 test-file decoding on Chinese Windows, and unclosed SQLite test fixtures.
- A bounded small-body drain for unsupported uploads prevents an unread-body
  Windows connection reset from masking the rejection; 20 repeated requests passed.
- Windows helper scripts parse in Windows PowerShell 5.1. The standalone
  diagnostic correctly detects SSH session 0 versus Desktop session 1.
- After one manual, non-elevated desktop-helper launch, an SSH-issued diagnostic
  request successfully executed in Desktop session 1. The schema-3 Raw scan read
  154 controls with no truncation or provider errors. It found a visible, enabled,
  keyboard-focusable Edit control at depth 26 with a writable ValuePattern; no
  input value was read during diagnostics.
- Schema 4 identified the empty composer's Chinese voice/dictation buttons. After
  manually entering the fixed test draft it found a unique Chinese Send button
  with InvokePattern, separate from dictation.
- The temporary interactive-token bootstrap started the limited helper in Desktop
  session 1 from SSH session 0; no temporary registration remained afterward.
- One opt-in native Invoke sent the fixed draft. The resulting Desktop rollout
  contained that user message, a final assistant acknowledgement and task_complete.
  No app-server was used to start that turn and no Desktop restart was needed.
  This was the initial send-only milestone. Native text routing and guarded
  native Stop have since passed the additional checks documented above; both
  remain opt-in preview features.

The first interactive Control-view probe completed without errors in session 1
but exposed only 13 outer-window nodes, with no Edit/Document or allowlisted
Send/Stop label. The subsequent Raw-view probe exposed 127 nodes and the Chrome
web document but hit the old 24-level depth limit before finding an input.
Its one matching Restore label was a window-caption control, not a task action.
Schema 3 addresses depth, caption exclusion, explicit partial-scan status and
direct UTF-8 output. The initial rescan found the editable candidate beyond the
previous depth limit but no Send/Stop label because the visible button was voice.
Schema 4 and the fixed-draft test above distinguish that state from an actual
Send button. The diagnostic and fixed smoke test have no coordinate-click,
typing or focus-changing fallback; the separate ordinary-text adapter explicitly
requests native window activation and ValuePattern insertion as documented above.

The [official Python embeddable package](https://www.python.org/downloads/release/python-31315/)
was SHA-256 verified before use. No global Python install, public listener,
persistent scheduled task, Desktop restart, or change to the Mac service was performed.
Background model execution, external-writer Desktop history synchronization,
attachments and wider lock/localization cases remain separate
acceptance steps, not implied by the native text-send and Stop tests above.
