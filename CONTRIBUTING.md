# 贡献说明

[中文](CONTRIBUTING.md) | [English](CONTRIBUTING.en.md)

欢迎帮助改进这个项目。为了让仓库保持“新手可部署、安全边界清楚、最小必要代码”的方向，请优先遵守下面几条。

## 先看什么

开始前，建议先阅读：

- `README.md`
- `docs/DEPLOYMENT.zh-CN.md`
- `SECURITY.zh-CN.md`

## 适合的贡献方向

- 修复手机查看 / 聊天控制相关 bug
- 改进部署脚本和自检脚本
- 改进中文优先的新手文档
- 增强单用户、自托管场景下的安全性

## 暂不鼓励的方向

- 默认改成公网直接暴露
- 未经审计就放宽认证和设备审批
- 把仓库改造成多人 SaaS
- 提交完整上游源码、运行日志、数据库或打包产物

## 提交前最低检查

请至少做这些事：

1. 确认没有真实 secret、日志、数据库、私有域名或个人路径
2. 运行 `scripts/check-open-source-tree.ps1`
3. 如果改了覆盖层，运行 `scripts/smoke-test-override-flow.ps1`
4. 如果改了桌面工具，运行 `python -m py_compile mobile_codex_control.py`
5. 如果改了文档，检查中英文入口是否一致

## Pull Request 建议

- 一次 PR 只解决一类问题
- 标题尽量清楚，例如：
  - `fix: repair device approval polling`
  - `docs: clarify first-time Tailscale setup`
- 如果改动了安全边界，请在 PR 里明确写出风险

## 安全问题

如果你发现的是认证绕过、设备信任绕过、secret 泄露等问题，请不要直接公开细节。  
请先按 `SECURITY.zh-CN.md` 中的说明处理。

