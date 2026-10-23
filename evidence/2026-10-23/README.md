# PR 证据归档（2026-10-23）

本目录是 [coatcn1/MaaBanGDream](https://github.com/coatcn1/MaaBanGDream) 上
2026-10-23 提交的 PR 的真机/日志证据。它不是产品代码，放在独立证据分支，
不进入任何 PR 分支或 `main`。

## 设备

- MuMu 12，配置 2：`127.0.0.1:16448`，1280×720，DPI 240，60 fps
- 配置 1：`127.0.0.1:16416`（用于双设备竞演对照）
- 游戏：`com.bilibili.star.bili`，两台 UID 均为 `10046`，root 已开
- 引擎：Native realtime，Profile `expert-20261004064704.json`，流速 5.07

## 目录

| 目录 | 对应 PR | 内容 |
| --- | --- | --- |
| `pr-103-105/` | #103、#105 | uidrange 断网后端与自动跳车的日志、checkpoint、summary、处罚截图 |
| `pr-104/` | #104 | LIVE BOOST 面板关闭失败的日志栈 |
| `pr-106/` | #106 | 2026-10-21 真机「匹配中」卡死的日志与截图（修复前复现） |

## 处理说明

- 日志中的本机绝对路径已替换为 `<workspace>`。
- 未包含账号凭据、ADB 序列号或完整 Profile。
- 所有日志均为 MFA 原始输出的节选；行首时间戳和 `[inst=...]` 保留。
- 证据只来自 MuMu 12。雷电（LDPlayer）上的 iptables 回退路径没有真机回归，
  相关 PR 正文已注明。
