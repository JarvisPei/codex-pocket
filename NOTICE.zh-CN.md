# 说明

[中文](NOTICE.zh-CN.md) | [English](NOTICE.md)

这个仓库不是完整替代上游，而是一个“在上游基础上追加手机控制与安全收敛”的辅助层。

如果你是第一次接触这个项目，请理解成：

- 你先准备上游 `siteboon/claudecodeui`
- 再把当前仓库的覆盖文件应用上去
- 最后按照 README/部署文档启动和使用

这个辅助层是建立在上游项目基础上的：

- 上游项目：`siteboon/claudecodeui`
- 适配测试版本：`v1.25.2`
- 上游许可证：`GPL-3.0`

当前目录刻意不包含以下内容：

- 个人数据库
- 运行日志
- 打包好的二进制
- `node_modules`
- 私有主机名或个人路径
- 超出最小必要范围的完整上游源码快照

这样做的目的，是尽量降低公开仓库里误带个人环境数据的风险。

在公开发布前，请先检查：

- [`docs/OPEN_SOURCE_RELEASE_CHECKLIST.zh-CN.md`](docs/OPEN_SOURCE_RELEASE_CHECKLIST.zh-CN.md)

如果你曾经在私有部署中使用过真实 secret、旧版 query token 或其他可能泄露会话的方案，请先在真实运行环境里完成 secret 轮换。
