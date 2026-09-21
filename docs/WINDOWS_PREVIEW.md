# Windows 开发预览 / Windows developer preview

[中文上手指南](WINDOWS_QUICKSTART.md) · [English quick start](WINDOWS_QUICKSTART.en.md)

Windows 支持仍处于开发预览阶段，不代表与 macOS 功能完全一致。
请先用非敏感测试任务验证自己的 Codex Desktop 版本。

Windows support is a developer preview, not full macOS feature parity.
Validate your Desktop version with disposable, nonsensitive tasks first.

## 安装与日常使用 / Setup and daily use

- Windows 11、原生 Python 3.11+、Windows PowerShell 5.1、已登录的 Codex Desktop。
- 电脑和手机连接同一 Tailscale 网络，配置私有 Tailscale Serve HTTPS。
- 首次双击仓库根目录的 **Start Pocket.cmd**；以后使用桌面 **Codex Pocket** 快捷方式。
- 托盘同时管理网页服务和桌面助手，无需保留终端；右键可打开配对二维码或退出。
- 不安装开机自启，不下载 Python，不修改系统睡眠或 Tailscale 设置。

Use **Start Pocket.cmd** once, then the desktop **Codex Pocket** shortcut.
The tray owns both services; no terminal needs to stay open. It does not install
autostart, bundle Python, or configure sleep/Tailscale. See the quick start for
prerequisites, pairing and troubleshooting.

## 支持范围 / Supported preview scope

| 功能 / Feature | 状态 / Status |
| --- | --- |
| 二维码配对、设备撤销 / QR pairing and revocation | 支持 / Supported |
| 项目、历史、置顶和已读 / Projects, history, pins and read state | 支持；依赖 Desktop 数据格式 / Supported, Desktop-format dependent |
| 原生发送、切换和停止 / Native Send, switching and Stop | 已有 Windows 11 实机验证 / Initially verified on Windows 11 |
| Project / Recents 新建任务 / Task creation | 托盘入口启用 / Enabled by the tray entry |
| 文件与图片 / Files and images | 工作目录中的文件路径交付，非原生图片卡片 / Workspace paths, not native image chips |
| 模型设置和 usage / Model settings and usage | 依赖当前账号和 CLI 支持 / Subject to account and CLI support |
| 原生继续 / Native Resume | 默认关闭：存在空回复问题 / Off by default: empty-reply limitation |
| 锁屏、熄屏、睡眠 / Lock, display-off and sleep | 不承诺可靠；保持清醒并解锁 / Not guaranteed; keep Windows awake and unlocked |

任务栏唤起需要主动开启；它可能将 Codex 切到前台，但不会解锁电脑。
任务执行与发送回执是不同状态；回执未确认时不要反复发送。
Codex 更新可能改变控件、CLI 路径或私有数据格式，暂不保证跨版本兼容。

Taskbar activation is opt-in and can bring Codex forward, but never unlocks Windows.
An unconfirmed delivery receipt is not proof that a task did not start; avoid
repeated sends. Desktop updates may require compatibility fixes.

## 安全与数据 / Security and data

- 只监听本机回环地址，通过 tailnet 私有 HTTPS 访问；不要使用公网 Funnel。
- 使用当前 Windows 用户的 DPAPI 加密凭据，并检查数据目录权限。
- 拒绝异常权限、符号链接和 junction，不通过放宽权限修复失败。
- 附件保留在任务工作目录的 `.codex-pocket-attachments` 中，不会自动清理；
  请勿把私人附件提交到 GitHub。
- 数据和配对位于 `%LOCALAPPDATA%\CodexPocket`；升级不要删除这个目录。
- 退出 Pocket 不会关闭 Desktop 或停止已交付的 Codex 任务。

Credentials use current-user DPAPI and restricted directory ACLs. Unexpected
permissions and reparse paths fail closed. Uploads remain in the task workspace;
do not commit private attachments. Keep `%LOCALAPPDATA%\CodexPocket` when upgrading
to preserve pairing. Exiting Pocket does not stop tasks already handed to Desktop.

## 维护者检查 / Maintainer checks

Windows 专用实现和脚本位于 `platforms/windows/`，测试位于 `tests/windows/`。
共享网页、配对和协议实现仍复用仓库根目录代码。
Windows-specific code lives in `platforms/windows/`; tests live in `tests/windows/`.
Browser, pairing and protocol components are shared with macOS.

在仓库根目录运行 / Run from the repository root:

```powershell
py -3 -m unittest discover -s tests -p "test_*.py"
node --test tests/test_web_capabilities.cjs tests/test_web_pairing.cjs tests/test_web_read_state.cjs
```

GitHub Actions 另在 Windows 上检查 DPAPI、ACL、PowerShell、启动器和界面识别的隔离样例。
Mac 上跳过的 Windows 专属测试不等于通过。自动测试不发送真实模型请求；
手动验收脚本需要明确指定可丢弃的任务并提供确认参数，不属于普通安装流程。

CI adds Windows-native checks. Skipped native checks on macOS are not Windows
acceptance. Automated tests do not send model requests; manual acceptance tools
require explicit confirmation and disposable task IDs.

报告问题时请提供 Windows / Python / Codex 版本、复现步骤和脱敏错误码；
不要分享配对链接、密钥、完整对话或私人附件。
For bug reports, include versions, reproduction steps and sanitized error codes,
never pairing URLs, credentials, conversations or private attachments.
