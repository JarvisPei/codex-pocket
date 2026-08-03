# Codex Pocket

[English](README.en.md)

在 Android 手机上查看和控制 Mac 上的 Codex Desktop，同时让 Desktop 保持任务的真正执行者。

Codex Pocket 在 Mac 本机运行一个窄接口 Bridge，通过 Tailscale Serve 暴露给自己的 tailnet。手机不需要登录 ChatGPT，也不会获得 Codex 凭据、通用终端或远程桌面权限。

## 已实现

- 按 Codex Desktop 的 Projects 和 Recents 展示持久化任务。
- 在抽屉顶部聚合运行中、需要处理、Desktop 未读和已置顶的任务；手机打开任务会同步已读状态，同时保留原有 Project/Recents 归属。
- 在 Project 或 Recents 中新建任务，并交给 Desktop 执行第一条指令。
- 查看最近历史、最终回复及按原位置穿插的 `Working` / `Worked` 活动摘要。
- 从手机向已有 Desktop 任务发送后续指令。
- Mac 明确处于锁屏状态时，纯文本新任务和后续指令可由已连接的 app-server 在后台运行，并继续写入 Desktop 历史。
- 从手机附加文件或图片；每条最多 4 个、单个最多 20 MB。
- 查看运行、暂停、完成状态，并按任务 ID 切换后安全停止任意运行中的 Desktop 任务。
- 修改任务的模型、推理等级和 Fast 服务档位。
- 查看剩余 Usage。
- 处理一次性命令/文件批准与结构化用户问题。
- 五分钟单次配对二维码、每台设备独立凭据和设备撤销。
- 长对话增量刷新、新内容提示和可拖动滚动条。
- 可选 Android 系统通知：任务完成、暂停或需要确认时提醒，点击可回到对应任务。
- 可选热点本地 HTTPS：手机同时为 Mac 提供热点时，绕过远端 DERP 中继直接访问。

系统通知默认关闭，需要在手机抽屉中主动开启。通知只包含任务标题和状态，不包含回复正文。当前实现依赖 Codex Pocket 页面仍打开或保留在浏览器后台；彻底结束浏览器进程后不会继续轮询，也没有把通知内容交给第三方推送平台。

锁屏后台模式只在 Bridge 明确检测到 macOS 已锁屏时启用，并且目前仅支持纯文本。包含附件的请求会要求先解锁；锁屏状态未知时继续采用原有 Desktop 路径。后台任务会持久化到 Codex 历史；新建任务可在 Desktop 首次打开时正常读取，已被 Desktop 加载过的旧任务可能需要手动重启 Codex 才能显示外部写入的新 turn。Bridge 不会自动切换或重启 Desktop。使用该模式时需要让 Mac 保持系统唤醒，显示器可以熄灭。

## 工作方式

```text
Android 浏览器
      │
      │ Tailscale HTTPS（仅 tailnet）
      ▼
Tailscale Serve
      │
      │ 127.0.0.1:4317
      ▼
Codex Pocket Bridge
      ├── 只读/受限 Codex app-server：任务列表与持久化历史
      └── macOS Accessibility Helper：切换任务、发送、继续和停止
                              │
                              ▼
                       Codex Desktop
```

Bridge 始终只监听 loopback。Codex app-server 的原始传输、ChatGPT 登录信息和 macOS 通用输入控制均不会暴露到网络。

## 环境要求

- macOS 与 Codex Desktop（`/Applications/ChatGPT.app`）
- Apple Command Line Tools，用于编译专用 Accessibility Helper
- Tailscale，推荐使用 Serve 提供 tailnet 内 HTTPS
- Android 或其他现代移动浏览器

MacBook 需要长期锁屏或合盖远程运行时，推荐安装免费的 [Amphetamine（Mac App Store）](https://apps.apple.com/app/amphetamine/id937984704?mt=12) 来保持系统唤醒。建议开启 **Allow Display Sleep**；如果需要合盖继续运行，则关闭 **Allow system sleep when display is closed**。阻止系统睡眠会增加耗电和发热，长时间使用时建议连接电源并保证散热。Amphetamine 只是可选的电源管理辅助，不替代 Tailscale 或 Codex Pocket 的安全设置。

## 安装 Bridge

```sh
git clone https://github.com/JarvisPei/codex-pocket.git
cd codex-pocket
zsh scripts/install-mac-bridge-launch-agent.sh
```

安装脚本会：

- 安装并启动用户级 LaunchAgent；
- 在 macOS Keychain 中使用 Bridge 主凭据；
- 构建 `~/Applications/MobileCodexBridgeHelper.app`；
- 让服务监听 `127.0.0.1:4317`。

首次安装后，在“系统设置 → 隐私与安全性 → 辅助功能”中允许 `Mobile Codex Bridge Helper`。底层 Bundle、LaunchAgent 和 Keychain 标识暂时保留旧的 `mobile-codex-bridge` 名称，以兼容已经授权的安装。

本机检查：

```sh
curl http://127.0.0.1:4317/health
```

## 通过 Tailscale 访问

只把本地 Bridge 暴露到自己的 tailnet，不要直接监听局域网或公网地址：

```sh
tailscale serve --bg http://127.0.0.1:4317
tailscale serve status
```

随后使用 Tailscale 提供的 HTTPS 地址访问。

## 手机热点本地直连（可选）

当 Android 手机同时作为热点和控制端时，运营商 NAT 可能让 Tailscale 退回远端 DERP。保持手机与 Mac 都在该热点拓扑下，在 Mac 运行：

```sh
zsh scripts/install-local-hotspot-proxy.sh [port]
```

安装器会记录当前 Mac 热点 IP、手机网关和 Wi-Fi 接口，并创建独立的 TLS 反向代理：

- 主 Bridge 仍只监听 `127.0.0.1:4317`；
- 本地代理仅在记录的热点网关与接口同时匹配时监听 `https://<热点中的 Mac IP>:4318/`；
- TLS 根证书带有 critical name constraints，只允许该热点 IP 与 `codex-pocket.local`；
- 本地入口继续使用每台设备独立凭据，不暴露 Keychain 主凭据。

安装后先通过原 Tailscale 页面打开抽屉，点击底部的 `⌁`：

1. 下载 CA 证书，并在 Android 的“安装 CA 证书”设置中安装；
2. 返回页面，再次点击 `⌁`，选择“切换到本地”；
3. 页面会使用五分钟单次票据为本地 HTTPS origin 建立独立设备凭据。

离开该热点后，本地代理会暂停监听；从本地页面可切回保存的 Tailscale origin。证书私钥只保存在 Mac 用户目录，权限为 `0600`。

## 配对手机

在 Mac 上生成五分钟有效、只能使用一次的二维码：

```sh
swift scripts/create-pairing-qr.swift \
  https://your-mac.your-tailnet.ts.net/ \
  /private/tmp/codex-pocket-pairing.png
```

用手机扫描并完成配对。设备凭据保存在该浏览器的 `localStorage`，关闭标签页或重启手机不会要求重新配对。二维码属于临时秘密，配对完成后应删除。

查看或撤销设备：

```sh
python3 scripts/manage-bridge-devices.py list
python3 scripts/manage-bridge-devices.py revoke <device-id>
```

## 安全边界

- 仅允许绑定 `127.0.0.1`。
- 可选热点代理是独立 TLS 进程；只反向代理到 loopback，并锁定预配置的 IP、网关、接口与 Host。
- 不提供 CORS、通用 shell、任意 JSON-RPC 或原始 app-server 代理。
- 每台手机拥有独立随机凭据；Mac 只保存其 SHA-256 摘要。
- Keychain 主凭据不会发送到手机。
- Desktop 发送前验证精确任务 ID、任务标题、空输入框和唯一 Send 控件。
- 附件按配对设备隔离，只写入权限为 `0700` 的专用目录；一小时过期，Helper 拒绝目录外路径。
- Stop 必须经过显式确认，并且只能按下唯一语义 Stop 控件。
- 日志只记录元数据，不记录 Authorization 或完整指令正文。
- 远程链接与 Markdown 使用安全、无 `innerHTML` 的渲染路径。

更完整的协议与威胁边界见 [macOS Bridge 说明](docs/MAC_BRIDGE.md)。

## 测试

```sh
python3 -m unittest tests.test_mac_bridge
```

当前测试覆盖设备配对、附件归属与路径保护、Project/Recents 归属、Desktop 发送与停止、历史序列化、活动分组、模型设置、Usage、新建任务和失败保护。

## 当前限制

- Bridge 依赖 Codex Desktop 当前的 Accessibility 结构；Desktop UI 大幅变化时可能需要更新 Helper。
- 只支持单用户、单 Mac 的私人部署，不是多人 SaaS。

## 致谢

Codex Pocket 最初基于 [StarsTom/mobileCodexHelper](https://github.com/StarsTom/mobileCodexHelper) 的开源工作展开。感谢 StarsTom 提供了从手机远程访问本机 Codex 的初始思路与实现基础。

## License

GPL-3.0，见 [LICENSE](LICENSE)。
