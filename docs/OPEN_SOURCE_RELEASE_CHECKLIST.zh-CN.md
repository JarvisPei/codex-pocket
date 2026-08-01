# 开源发布检查清单

[中文](OPEN_SOURCE_RELEASE_CHECKLIST.zh-CN.md) | [English](OPEN_SOURCE_RELEASE_CHECKLIST.md)

## 第一次公开推送前必须完成

- [ ] 确认仓库内没有 `vendor/`、`node_modules/`、`dist/`、`build/`、`.runtime/`、`tmp/`
- [ ] 确认没有 `.db`、`.sqlite*`、`.log`、`.env`、`.exe`
- [ ] 确认文本文件中没有真实 JWT secret、API key、私钥或会话值
- [ ] 将真实主机名、tailnet 名称、本机用户路径、私有 IP 替换为占位符
- [ ] 运行 `powershell -ExecutionPolicy Bypass -File scripts/check-open-source-tree.ps1`
- [ ] 如果历史私有版本曾暴露 secret，先轮换真实运行环境中的 secret
- [ ] 确认 `README.md` 与实际安装步骤一致
- [ ] 确认上游归属说明和 `LICENSE` 已保留

## 建议在打 tag 前完成

- [ ] 用一份干净的上游 `v1.25.2` 实测 `scripts/smoke-test-override-flow.ps1`
- [ ] 在新机器环境下测试本地启动 / 停止脚本
- [ ] 执行 `python -m py_compile mobile_codex_control.py`
- [ ] 如果要发布 EXE，执行 `scripts/package-mobile-codex-control.cmd`
- [ ] 补 issue 模板和 release note，说明安全边界
