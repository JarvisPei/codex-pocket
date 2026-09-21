# Windows 11 上手指南（开发预览）

[English](WINDOWS_QUICKSTART.en.md) · [支持范围与限制](WINDOWS_PREVIEW.md)

本指南对应**包含 Windows 文件的预览源码**，不是安装包或正式稳定版。先确认目录里有
`platforms/windows/bridge.py` 和 `platforms/windows/scripts/windows-start-desktop-helper.ps1`；旧版 macOS 源码不能照此启动。
不需要 AI、SSH、远程桌面或管理员身份运行 Pocket，也不需要在手机登录 ChatGPT。

## 1. 准备

- Windows 11；Codex Desktop 已登录，电脑保持清醒、登录且解锁。
- 原生 Windows Python 3.11+、Windows PowerShell 5.1（不是 WSL）。
- 电脑和手机均安装、登录并连接同一个 Tailscale 网络。
- 把预览源码放在自己的目录；后续命令都在该源码目录执行。

打开**普通 PowerShell**，进入源码目录，确认 Python：

```powershell
$pythonBinary = (py -3 -c "import sys; print(sys.executable)").Trim()
if ($LASTEXITCODE -ne 0) { throw '请先安装原生 Windows Python 3.11+' }
& $pythonBinary --version
```

如果没有 `py`，将 `$pythonBinary` 设置成已安装的 `python.exe` 绝对路径。
Pocket 不要求额外 pip 包。不要把 Microsoft Store 启动别名、WSL 或 Desktop 图形程序当成原生 CLI。

推荐入口会自动查找并验证 Codex CLI，通常无需提前手动找路径。
只有自动识别失败或采用下面的高级启动方式时，才需要手动指定 `codex.exe`。

## 2. 启动 Pocket（推荐：托盘入口）

首次双击源码根目录的 **`Start Pocket.cmd`**，它会用 Windows 自带 .NET 编译器生成一个小型图形启动器，
创建/升级桌面的 **Codex Pocket** 快捷方式并启动。安装窗口可能短暂出现，但无需一直保留。
不下载依赖，不修改系统默认终端、执行策略或开机启动项。已有同名但不属于 Pocket 的快捷方式不会被覆盖。
首次选择上面确认的 `python.exe`。
Pocket 会查找标准安装目录中的 `codex.exe`：只有唯一候选且版本检查通过才自动选择，
多个候选或检查失败时明确提示手动选择。启动连接成功后会记住路径。也可从普通 PowerShell 首次传入 Python 路径：

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy RemoteSigned -File platforms\windows\scripts\windows-pocket-tray.ps1 -PythonBinary $pythonBinary
```

之后每天只需双击桌面的 **Codex Pocket**，不必单独打开桌面助手或保留终端。
日常快捷方式通过图形程序直接托管托盘脚本，不启动 `powershell.exe` 控制台宿主，也不依赖 `-WindowStyle Hidden` 隐藏终端。
通知区域（可能在隐藏图标里）的 Pocket 图标同时管理网页服务和助手，**没有两小时/四小时到期限制**。
右键可以打开配对二维码、退出 Pocket；退出会等待正在交付的请求结束，不关闭 Codex，也不停止已经交给 Desktop 的任务。
首次启动复用或初始化当前用户凭据，已有手机配对不会被重置。

仍需先打开已登录的 Codex，并保持 Windows 清醒、解锁。托盘入口不设置开机自启、不修改睡眠、
不配置 Tailscale，也不打包 Python。首次若已有旧 Bridge/助手，请等发送结束后关闭旧启动窗口；
新入口不会强制接管或结束不属于自己的进程。重复双击只提示已有托盘实例。
上面的直接 PowerShell 命令用于首次传参/排错，可能保留终端；日常使用桌面快捷方式。
升级启动器或移动源码前先从托盘退出 Pocket，再运行 `Start Pocket.cmd` 修复快捷方式。
生成的启动器位于 `%LOCALAPPDATA%\CodexPocketLauncher`。

托盘入口启用 Send/Stop、新建任务和附件路径，不启用原生 Resume。
如需任务栏唤起，在上面的托盘 PowerShell 命令末尾添加 `-EnableTaskbarActivation`；该选择会记住。
修改时先退出托盘，再带 `-EnableTaskbarActivation:$false` 或 `:$true` 启动。
Codex 更新后旧路径失效，会再次按上述规则查找并验证替代路径，不需要每次手动找文件。
不会扫描 PATH、按修改时间猜版本或重置配对；找不到、候选不唯一或验证失败才提示选择。
取消文件选择会退出 Pocket，不保存未验证路径，也不再弹出含糊的 “Setup cancelled” 错误。
Python 路径失效仍需重新选择。路径配置在 `%LOCALAPPDATA%\CodexPocket\launcher.json`。

附件保留在目标任务目录 `.codex-pocket-attachments`，作为路径交给模型读取，不是原生图片卡片。
这些文件不会自动删除，也不要把包含私人附件的工作目录直接提交到 GitHub。

### 高级：独立启动各组件（不要与托盘入口同时运行）

日常使用托盘入口可跳过本节。手动启动或排错时，先找到当前 Codex 安装中的 **`codex.exe`**。
已测试安装位置可用下面的只读命令列出：

```powershell
Get-ChildItem -LiteralPath "$env:LOCALAPPDATA\OpenAI\Codex\bin" -Filter codex.exe -File -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
```

安装位置随版本可能不同；没有结果时需要提供实际安装中的路径。多个结果不要随便选第一项，
确认使用当前安装的 CLI。将下面的示例替换成实际路径（不是 `ChatGPT.exe` 或 `.cmd`）：

```powershell
$codexBinary = 'C:\实际安装路径\codex.exe'
& $pythonBinary -m platforms.windows.bridge doctor --codex-binary $codexBinary
if ($LASTEXITCODE -ne 0) { throw 'CLI 检查未通过，先按输出排错' }
```

`doctor` 只做连接检查，不创建任务或发送模型请求。也可将已确认的路径用
`-CodexBinary $codexBinary` 传给上面的托盘命令。
独立启动前，先打开 Codex Desktop，确保输入框没有未保存草稿。

```powershell
& $pythonBinary -m platforms.windows.bridge init
if ($LASTEXITCODE -ne 0) { throw '凭据初始化失败，不要删除旧凭据或放宽权限' }
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File platforms\windows\scripts\windows-start-desktop-helper.ps1 -PythonBinary $pythonBinary -EnableNativeSend -EnableNativeStop
if ($LASTEXITCODE -ne 0) { throw '桌面助手未启动，请先排错' }
& $pythonBinary -m platforms.windows.bridge serve --codex-binary $codexBinary --native-text-send --native-new-tasks --attachment-paths
```

最后一行会持续运行，**仅此独立启动方式需要保持该 PowerShell 窗口打开**。
这条命令显式开启已测试的新建任务和文件路径模式；
若只想控制已有任务、发送文字，去掉 `--native-new-tasks --attachment-paths`。
裸 `serve` 是另外一种后台执行模式，不是本指南的 Desktop 原生模式。

只启用 Send/Stop，不启用仍有空回复问题的原生 Resume。

如需在 Codex 被其他窗口盖住时尝试自动唤起，可在**助手命令**末尾额外添加
`-EnableTaskbarActivation`，再在助手空闲时重新运行该助手命令。
这会允许在普通激活失败时点击经过身份检查的 Codex 任务栏图标；默认关闭，
使用时避免同时在电脑操作键鼠。它不会自动解锁电脑。

## 3. 私有 HTTPS（Tailscale 配置窗口）

先检查已有配置，不要覆盖其他应用正在使用的 Serve 路由：

```powershell
tailscale serve status
```

如果尚未配置 Pocket，按 [Tailscale 的 Windows 指引](https://tailscale.com/docs/reference/examples/serve)
在**单独的管理员 PowerShell** 中执行以下 Tailscale 命令，然后关闭这个管理员窗口。
Pocket、Codex 和配对工具仍在普通用户窗口运行。

```powershell
tailscale serve --bg http://127.0.0.1:4317
tailscale serve status
```

若提示开启 HTTPS，按命令显示的 Tailscale 链接确认后再检查状态。
目标应是 `http://127.0.0.1:4317`，手机使用显示的 `https://…ts.net` 地址。
[Serve 仅在 tailnet 内提供访问](https://tailscale.com/docs/features/tailscale-serve)；不要改用公网 Funnel，
也不需要开放公网 4317 端口。`--bg` 只让 Tailscale 转发持续运行，不会启动 Pocket 或桌面助手。

## 4. 扫码配对

使用托盘入口时，右键 Pocket 图标 → **Pair phone / QR code** 即可。
首次安装启动也会尝试自动打开配对页面。以下命令是手动排错的备用方式。

新建一个**普通 PowerShell**，进入同一源码目录，先检查组件：

```powershell
py -3 -m platforms.windows.bridge doctor --codex-binary 'C:\实际安装路径\codex.exe' --native
```

检查通过后：

```powershell
py -3 scripts/pair-device.py
```

首次 `init` 后启动 Bridge 会尝试自动打开配对页面；已经打开就不必再启动一个。
如果自动检测地址失败，可明确指定 `tailscale serve status` 显示的 HTTPS 地址：

```powershell
py -3 scripts/pair-device.py --url https://your-pc.your-tailnet.ts.net
```

在电脑页面检查地址，再用手机扫码，点击“确认配对”。二维码五分钟有效、只能用一次；
过期就在电脑页重新生成。手动配对码也在该页面中。不要把二维码、配对链接或本地显示页链接公开。
普通 Python 没有 `py` 时，在该排错窗口使用 `& 'C:\实际路径\python.exe'` 代替 `py -3`。

配对成功后收藏 **不含 `#pairing=…` 的 HTTPS 首页**。同一浏览器的配对会保留；
关闭二维码页不会断开已配对设备。无痕模式、清除站点数据或换浏览器可能需要重新配对。

## 5. 第一次测试与日常恢复

先在 Desktop 建一个测试 Project。手机点它旁边的「＋」，写“只回复收到”并发送；
应看到任务归入该 Project，电脑收到消息，手机出现回复。再附一张非敏感测试图片试一次。
Recents 旁的「＋」创建不属于 Project 的任务。

显示“等待 Desktop 回执”时不要另建任务或重复点发送。页面会自动核对原请求，不再次发送；
最终仍未确认时，保留草稿和请求编号，先查看历史。消息运行状态与发送确认是两件事。

| 场景 | 怎么恢复 |
| --- | --- |
| Windows 重启或退出登录 | 登录、解锁并打开 Codex，双击桌面 Codex Pocket；原配对保留 |
| 退出了托盘 Pocket | 再双击桌面 Codex Pocket，不用另外启动助手 |
| 使用高级独立模式，只关闭了 Bridge 窗口 | 助手仍有效时，重新运行第 2 步高级部分的 `serve`；有疑问先 `doctor --native` |
| 使用旧独立助手，运行满四小时 | 托盘入口没有此限制；独立测试助手仍需空闲时重开 |
| 使用 `windows-start-phone-preview.ps1` 的测试者 | 该旧测试启动器最多两小时；推荐托盘入口没有这个计时器。空闲时退出旧组件后可改用托盘入口 |
| Codex 更新后找不到 CLI 或发送控件 | 托盘重开时会自动检查 CLI 路径；仍失败再手动选路径并运行 `doctor --native`。控件问题可能需要兼容更新，不要盲目放宽权限 |

重启电脑后 Tailscale 也要处于已连接状态；Serve 配置可能仍保留，但 Pocket 不会自启动。
锁屏、熄屏、合盖运行暂不承诺可靠，继续放在后续适配范围内。

## 常见问题

- **地址打不开**：在电脑运行 `Invoke-RestMethod http://127.0.0.1:4317/health`。
  本地也失败就先查 Bridge；本地成功再查 Tailscale 连接和 Serve 目标。不要先重置配对。
- **能看历史却不能发**：运行 `doctor --native`，确认助手的 Send 已开启、Windows 已解锁，
  并检查 Desktop 草稿和任务状态。保持 Codex 在前台，或明确开启上面的任务栏唤起选项。
- **新建按钮/附件按钮不可用**：托盘入口默认启用两者，先确认连接的是本次启动的 Pocket 并刷新手机页面；高级独立模式再检查第 2 步的完整 `serve` 参数。
- **端口已占用**：不要重复启动或结束不认识的进程。托盘模式先从 Pocket 图标菜单退出，等待退出后重开；只有高级独立模式才在自己的 Bridge 窗口空闲时按 Ctrl+C。若端口属于其他程序，先确认用途，不要强行结束。
- **脚本被策略阻止**：不要使用全局 `Bypass`、禁用终端防护或放宽数据目录 ACL；遵循组织批准的脚本执行方式。
- **需要分享诊断**：`doctor --native --json` 输出脱敏检查结果，不含密钥和对话；仍应在分享前检查内容。

升级旧预览版时，先从托盘退出 Pocket，更新源码后重新运行 `Start Pocket.cmd`。
脚本现在位于 `platforms/windows/scripts/`；不要继续调用旧的根目录 Windows 脚本。
不要删除 `%LOCALAPPDATA%\CodexPocket`，已有配对会保留。

这不是自启动安装器。支持范围和安全边界见[开发预览说明](WINDOWS_PREVIEW.md)。
