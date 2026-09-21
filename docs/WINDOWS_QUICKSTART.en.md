# Windows 11 quick start (developer preview)

[中文](WINDOWS_QUICKSTART.md) · [Detailed limitations and acceptance records](WINDOWS_PREVIEW.md)

Use a preview source checkout that contains `windows_bridge.py` and
`scripts/windows-start-desktop-helper.ps1`. This is not a packaged installer or
a stable Windows release; older macOS-only source cannot follow these steps.
Pocket does not require AI assistance, SSH, remote desktop, or a ChatGPT login on
your phone. Run Pocket as your regular Windows user, not Administrator.

## 1. Prepare

You need Windows 11, signed-in Codex Desktop, native Windows Python 3.11+,
Windows PowerShell 5.1 (not WSL), and Tailscale connected on both computer and
phone in the same tailnet. Keep Windows awake, signed in and unlocked.
Run the commands below from the preview source directory.

In a regular PowerShell window, find your Python executable:

```powershell
$pythonBinary = (py -3 -c "import sys; print(sys.executable)").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Install native Windows Python 3.11+ first' }
& $pythonBinary --version
```

If `py` is unavailable, set `$pythonBinary` to your actual `python.exe` path.
No additional pip packages are required. Do not use a Store launch alias or WSL.

The recommended launcher discovers and verifies the Codex CLI automatically.
You only need to locate `codex.exe` manually if discovery fails or you use the
advanced separate-component setup below.

## 2. Start Pocket (recommended: notification-area entry)

First double-click **`Start Pocket.cmd`** in the source root. It builds a small GUI
bootstrap using Windows' installed .NET compiler, creates/upgrades the desktop
**Codex Pocket** shortcut, and starts it. The setup terminal may briefly appear,
but need not stay open. There are no downloads, default-terminal changes, policy
changes or startup registrations; unrelated same-named shortcuts are preserved.
On first launch, select
the verified `python.exe`. Pocket checks the standard Codex CLI install folder:
only a unique candidate passing its version check is selected automatically.
Multiple candidates or validation failures require explicit selection. Successful
service startup remembers the paths. Alternatively, supply the Python path once:

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy RemoteSigned -File scripts\windows-pocket-tray.ps1 -PythonBinary $pythonBinary
```

For daily use, double-click the desktop **Codex Pocket** shortcut. Its GUI bootstrap
hosts the tray script directly without powershell.exe/ConsoleHost, rather than relying on WindowStyle Hidden.
The direct PowerShell command above is an advanced setup/debug entry and may retain
a terminal. Exit Pocket from its tray before updating the launcher or moving the
source folder, then run `Start Pocket.cmd` again to repair
the shortcut. Generated launcher files live in `%LOCALAPPDATA%\CodexPocketLauncher`.
The notification-area icon (possibly
under hidden icons) manages both the bridge and helper with **no two/four-hour
test expiry**. No terminal needs to remain open. Right-click to open QR pairing or
exit Pocket. Exit drains pending deliveries; it does not close Codex or stop tasks
already handed to Desktop. Existing phone pairing and credentials are preserved.

Keep Codex signed in and Windows awake/unlocked. This entry does not install
autostart, bundle Python, change sleep settings, or configure Tailscale. Close old
preview components when idle before using it: existing processes are never forcibly
adopted or terminated. Repeated launch only reports the existing tray instance.

Send/Stop, new tasks and attachment paths are enabled; native Resume remains off.
For optional verified taskbar activation, add `-EnableTaskbarActivation` to the
tray PowerShell command above; the choice is remembered. To change it, exit Pocket and relaunch
with `-EnableTaskbarActivation:$false` or `:$true`. After a Codex update removes the
saved CLI, Pocket repeats the same discovery and verification without resetting
pairing. It never searches PATH or guesses by modification time. Cancelling a file
selection exits without saving an unverified path or showing a second error dialog.
A missing Python path still requires selection. Path settings live in
`%LOCALAPPDATA%\CodexPocket\launcher.json`.

Uploads remain in the task workspace's `.codex-pocket-attachments` directory and
are sent as local paths, not native image chips. They are not automatically
deleted; do not accidentally commit private uploads to GitHub.

### Advanced: separate components (do not run alongside the tray entry)

Skip this section for daily tray use. For manual startup or troubleshooting,
locate the native **`codex.exe`** from your current Codex installation. This
read-only command lists candidates in the location observed on our test machine:

```powershell
Get-ChildItem -LiteralPath "$env:LOCALAPPDATA\OpenAI\Codex\bin" -Filter codex.exe -File -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
```

Installation paths can differ. If there are multiple candidates, identify the one
from the current installation instead of choosing the first. Replace the example
below with that absolute path, not the Desktop GUI executable or a `.cmd` shim:

```powershell
$codexBinary = 'C:\actual-installation\codex.exe'
& $pythonBinary windows_bridge.py doctor --codex-binary $codexBinary
if ($LASTEXITCODE -ne 0) { throw 'Resolve the CLI check failure first' }
```

The checker does not create tasks or call a model. You can also pass the verified
path to the tray command above with `-CodexBinary $codexBinary`.
Before starting separate components, open Codex Desktop and preserve/clear any
unsent composer draft.

```powershell
& $pythonBinary windows_bridge.py init
if ($LASTEXITCODE -ne 0) { throw 'Credential initialization failed; do not delete old credentials or loosen permissions' }
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File scripts\windows-start-desktop-helper.ps1 -PythonBinary $pythonBinary -EnableNativeSend -EnableNativeStop
if ($LASTEXITCODE -ne 0) { throw 'Resolve the desktop helper failure first' }
& $pythonBinary windows_bridge.py serve --codex-binary $codexBinary --native-text-send --native-new-tasks --attachment-paths
```

**Only this separate-component mode requires keeping its PowerShell window open.**
These explicit flags enable tested new-task creation and
local-file handoff. For existing-task text only, omit `--native-new-tasks` and
`--attachment-paths`. Bare `serve` selects a different background execution mode;
it is not the native Desktop workflow described here.

Native Resume is not enabled because an empty-reply limitation remains unresolved.

Optional: append `-EnableTaskbarActivation` to the **helper command** if you want
it to bring Codex forward when other windows cover it. Restart only the idle
helper to change this option. It permits a verified taskbar-icon click after
normal activation fails; it is off by default. Avoid simultaneous local keyboard
or mouse use. It never unlocks Windows.

## 3. Configure private HTTPS (separate Tailscale window)

Check `tailscale serve status` first. Do not overwrite a route used by another
application. If Pocket is not configured, follow the [Tailscale Windows
guidance](https://tailscale.com/docs/reference/examples/serve): run the following
Tailscale configuration in a **separate Administrator PowerShell**, then close
that window. Keep Pocket, Codex and pairing tools in regular user windows.

```powershell
tailscale serve --bg http://127.0.0.1:4317
tailscale serve status
```

If prompted, use the Tailscale link to enable HTTPS and check status again. The
proxy target should be `http://127.0.0.1:4317`; the phone uses the displayed
`https://…ts.net` address. [Serve is limited to your tailnet](https://tailscale.com/docs/features/tailscale-serve).
Do not use public Funnel or expose port 4317 publicly. `--bg` keeps Tailscale's
forwarder running; it does not start Pocket or the desktop helper.

## 4. Pair

With the tray entry, right-click Pocket → **Pair phone / QR code**. First install
also attempts to open pairing automatically. The commands below are a manual
diagnostic fallback.

Open another regular PowerShell in the same source directory. Replace the CLI
path explicitly; variables from other PowerShell windows are not shared:

```powershell
py -3 windows_bridge.py doctor --codex-binary 'C:\actual-installation\codex.exe' --native
```

After the checks pass:

```powershell
py -3 scripts/pair-device.py
```

The first Bridge start after a new `init` tries to open the pairing page
automatically; use that page if already open. If address detection fails:

```powershell
py -3 scripts/pair-device.py --url https://your-pc.your-tailnet.ts.net
```

Use the HTTPS origin shown by `tailscale serve status`. Check the address on the
computer, scan the QR on your phone, and confirm pairing. The code lasts five
minutes and works once; regenerate expired codes on the computer page. A manual
code alternative is provided. Keep QR codes, pairing URLs and the local display
URL private. Without `py`, substitute `& 'C:\actual-path\python.exe'` in this diagnostic window.

Bookmark the HTTPS home page **without `#pairing=…`**. The same browser retains
its pairing; closing the QR page does not disconnect paired devices. Private
browsing, clearing site data or switching browsers can require pairing again.

## 5. First test and daily recovery

Create a disposable Project in Desktop. On the phone, tap its adjacent **+** and
submit “Reply only received.” Check project placement, Desktop receipt and the
phone reply. Then try a nonsensitive image. The **+** beside Recents creates an
unassigned task instead.

While the phone waits for a Desktop receipt, do not create another task or tap
Send repeatedly. It automatically checks the original request without sending
again. Unresolved confirmation retains the draft/request ID; inspect history
first. Delivery confirmation is separate from whether a model is still running.

| Situation | Recovery |
| --- | --- |
| Windows reboot or logout | Sign in, unlock, open Codex and double-click the desktop Codex Pocket shortcut. Pairing is retained. |
| Pocket was exited from the tray | Double-click the desktop Codex Pocket shortcut; no separate helper launch is needed. |
| Advanced separate mode: only the Bridge window was closed | Run the advanced section's `serve` command again if the helper is still ready; use `doctor --native` to check. |
| Old standalone helper has run for four hours | The tray entry has no such limit. Restart standalone test helpers only when idle. |
| Using `windows-start-phone-preview.ps1` | This older test launcher lasts at most two hours; the recommended tray entry has no such timer. Exit the old components when idle before switching to the tray entry. |
| Codex updated | Reopening the tray entry checks the CLI path automatically. If that fails, select the path manually and run `doctor --native`. Changed UI controls may need a compatibility update; do not weaken permissions to force delivery. |

Tailscale must also be connected after reboot. Its Serve configuration may remain,
but Pocket does not start automatically. Lock, screen-off and lid-close behavior
are not yet promised reliable.

## Troubleshooting

- **Cannot open the phone URL:** check `Invoke-RestMethod http://127.0.0.1:4317/health`
  on the computer first. If that fails, inspect Bridge; otherwise inspect Tailscale
  connectivity and the Serve target. Do not reset pairing as the first step.
- **History works but Send does not:** run `doctor --native`; check helper Send,
  unlocked desktop, drafts and task state. Keep Codex foreground or explicitly
  enable the optional taskbar activation above.
- **New task/files disabled:** the tray entry enables both by default. Confirm that
  the phone is connected to the Pocket instance you just started and reload the page.
  For advanced separate mode, check the full `serve` flags in step 2.
- **Port occupied:** do not start another Bridge or kill unknown processes. In tray
  mode, exit Pocket from its icon menu and wait for it to exit before reopening.
  Only in advanced separate mode should you stop your own idle Bridge window with
  Ctrl+C. If another application owns the port, identify it rather than forcibly stopping it.
- **Script blocked:** follow your organization's approved script execution process;
  do not globally bypass policy, disable endpoint protection or loosen data ACLs.
- **Share diagnostics:** `doctor --native --json` produces sanitized checks without
  keys or conversations; review output before sharing.

There is no autostart installer. See the [preview guide](WINDOWS_PREVIEW.md) for
detailed acceptance scope and security boundaries.
