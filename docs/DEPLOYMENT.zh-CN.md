# 部署说明

[中文](DEPLOYMENT.zh-CN.md) | [English](DEPLOYMENT.md)

这份文档是给第一次部署的人看的。  
目标不是解释内部实现，而是帮助你一步一步把它跑起来，并让手机能够访问。

## 目标效果

部署完成后，你应该能做到这些事：

- 在电脑上启动本地 Codex 控制服务
- 在手机上通过私有地址访问网页
- 第一次登录新设备时，在电脑端手动批准
- 登录后在手机上继续查看和发送 Codex 消息

## 推荐环境

### 操作系统

- Windows 10 / 11

### 软件要求

- Python 3.11+
- Node.js 22 LTS
- Git
- nginx for Windows
- Tailscale（推荐）

### 为什么推荐 Tailscale

因为它最适合这个项目的目标：

- 只让你自己的设备访问
- 不需要自己折腾公网暴露
- 手机和电脑都很容易接入同一个私有网络

## 目录结构

建议把项目放成这样：

```text
mobileCodexHelper/
├─ deploy/
├─ docs/
├─ scripts/
├─ upstream-overrides/
├─ vendor/
│  └─ claudecodeui-1.25.2/
├─ mobile_codex_control.py
└─ requirements.txt
```

其中：

- `vendor/claudecodeui-1.25.2/` 是你自己下载的上游源码
- `upstream-overrides/claudecodeui-1.25.2/` 是本项目对上游的覆盖文件

## 第 1 步：准备上游源码

下载上游：

- `siteboon/claudecodeui`
- 版本：`v1.25.2`

然后放到：

```text
vendor/claudecodeui-1.25.2
```

## 第 2 步：应用覆盖文件

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/apply-upstream-overrides.ps1
```

执行后，本项目的补丁会覆盖到上游源码中。

如果你想先验证“公开仓库里的覆盖文件是否完整”，可以额外执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/smoke-test-override-flow.ps1 -UpstreamZip <你的上游zip路径>
```

它会在临时目录里自动解压上游 zip、执行覆盖，并检查所有覆盖文件是否都成功落到目标位置。

## 第 3 步：安装 Node 依赖

```powershell
cd vendor/claudecodeui-1.25.2
npm install
```

如果 `npm install` 失败，优先检查：

- Node 版本是否为 22 LTS
- 网络是否可正常访问 npm

## 第 4 步：安装 Python 依赖

如果你只打算直接运行桌面工具：

- 通常不需要额外依赖

如果你打算把桌面工具打包成 `.exe`：

```powershell
pip install -r requirements.txt
```

## 第 5 步：检查本地环境

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check-mobile-codex-runtime.ps1
```

你要重点看这几项：

- `UpstreamExists = True`
- `Node` 有值
- `Nginx` 有值
- 如果你准备用手机远程访问，`Tailscale` 也最好有值

## 第 6 步：启动服务

直接执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-mobile-codex-stack.ps1
```

默认会启动两个本地服务：

- 应用服务：`127.0.0.1:3001`
- nginx 代理：`127.0.0.1:8080`

## 第 7 步：打开桌面控制工具

```powershell
python mobile_codex_control.py
```

或者：

```powershell
scripts\launch-mobile-codex-control.cmd
```

你应该能看到：

- PC 应用服务状态
- nginx 状态
- Tailscale 状态
- 手机连接状态
- 待审批设备
- 已批准设备白名单

## 第 8 步：首次注册

在电脑浏览器中访问：

```text
http://127.0.0.1:3001
```

完成首次注册。

说明：

- 这是单用户模式
- 第一个注册账号会成为这套系统的管理账号

## 第 9 步：配置手机远程访问

### 方案 A：仅本地测试

先只在电脑浏览器上测试：

- 能否登录
- 项目列表是否正常
- 发送消息是否正常

### 方案 B：通过 Tailscale 私网访问

确保：

- 电脑已登录 Tailscale
- 手机也登录同一个 Tailnet

然后执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/enable-mobile-codex-remote.ps1
```

执行后：

- 在桌面控制工具里确认远程发布状态
- 用手机打开 Tailscale 给出的私有 HTTPS 地址

## 第 10 步：首次设备批准

新设备第一次登录时：

1. 手机端会显示等待批准
2. 电脑端桌面工具里会出现一条待审批设备
3. 你核对设备信息后点击批准
4. 手机端自动继续登录

这是本项目的重要安全机制，请不要跳过。

## 环境变量（可选）

通常默认就能跑，只有你路径特殊时才需要改。

### 可选环境变量

- `MOBILE_CODEX_UPSTREAM_DIR`
  - 指定上游 `claudecodeui` 目录
- `MOBILE_CODEX_NODE`
  - 指定 Node 可执行文件路径
- `MOBILE_CODEX_NGINX`
  - 指定 nginx 可执行文件路径
- `MOBILE_CODEX_TAILSCALE`
  - 指定 Tailscale 可执行文件路径
- `MOBILE_CODEX_ASCII_ALIAS`
  - 指定 ASCII 别名路径，用于处理某些 Windows 非 ASCII 路径问题

## 最短排障顺序

如果你不确定该先查哪里，按下面顺序最省时间：

1. 先跑 `scripts/check-mobile-codex-runtime.ps1`
2. 再确认电脑浏览器能打开 `http://127.0.0.1:3001`
3. 再确认桌面工具里 PC 服务和 nginx 都是正常
4. 然后才测试手机浏览器
5. 最后再测试封装 App / WebView

## 常见问题

### 启动脚本执行了，但页面打不开

检查：

- `127.0.0.1:3001` 是否响应
- `127.0.0.1:8080` 是否响应
- 桌面控制工具里两个服务是否都显示正常

### 手机浏览器可以，封装 App 不行

先确认封装壳是否支持：

- `localStorage`
- `Authorization` 请求头
- WebSocket
- Cookie

如果浏览器正常、封装壳异常，通常是壳兼容问题。

### 出现 502

优先查看：

- `tmp/logs/mobile-codex-app.stdout.log`
- `tmp/logs/mobile-codex-app.stderr.log`
- nginx 日志

## 部署完成后建议自查

如果你准备长期自己使用，建议至少确认下面这些事：

- 手机上第一次登录时，电脑端确实会弹出待审批设备
- 同一个账号换一台新手机时，旧设备不会自动继承信任
- 关闭 PC 服务后，手机端不会继续拿到可用会话
- 你没有把任何真实 `.env`、数据库、日志放进仓库

## 建议下一步

部署成功后，建议继续做：

- 把桌面工具打包成 `.exe`
- 配置你自己的 Tailscale 访问方式
- 根据你自己的环境调整 nginx / Caddy 配置
