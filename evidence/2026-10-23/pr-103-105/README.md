# 自动断网跳车真机证据（2026-10-23）

对应 PR：#103（uidrange 网络后端）、#105（自动断网跳车）。

## 条件

- 配置 2：`127.0.0.1:16448`，MuMu 12，1280×720，root 已开
- 游戏 UID `10046`；uidrange 规则 `pref=100`
- 竞演，Expert，Profile `expert-20261004064704.json`
- 触发方式：关闭显示器让生命归零

## AutoEnd（23:24，跳完结束任务）

| 时间 | 事件 |
| --- | --- |
| 23:24:44.752 | `NativeMinitouch session_stats ... state=cancelled release_confirmed=true` |
| 23:24:44.787 | `life_depleted=True completed=False`，`reason=生命归零` |
| 23:24:44.817 | `auto_jump=start streak=0/2` |
| 23:24:45.779 | `gate_backend=uidrange` |
| 23:24:57.724 | `DisconnectJump completed=true` |
| 23:24:58.363 | `auto_jump=completed streak=1/2` |
| 23:24:59.603 | 任务结束：`生命归零；自动断网跳车完成，任务结束` |

从 `auto_jump=start` 到 `completed=true`：**12.91 秒**。回到主页，无「功能限制」弹窗。

## AutoContinue（23:44，跳完继续）

| 时间 | 事件 |
| --- | --- |
| 23:44:01.791 | `life_depleted=True`，本局 777 帧 |
| 23:44:01.849 | `auto_jump=start streak=0/2` |
| 23:44:02.790 | `gate_backend=uidrange` |
| 23:44:14.919 | `DisconnectJump completed=true` |
| 23:44:15.593 | `auto_jump=completed streak=1/2` / `continue_round` |
| 23:44:23.846 | `round=4/5 reuse_room=false` |
| 23:45:32.190 | 下一局 `completed=True`（配置 1，930 帧） |
| 23:45:51.487 | 进度 `已完成 4/5` |

从 `auto_jump=start` 到 `completed=true`：**13.07 秒**。下一局正常开演并完成，无处罚。

## 反例（修复前，23:09）

| 时间 | 事件 |
| --- | --- |
| 23:09:41.524 | `auto_jump=start` |
| 23:09:42.496 | `gate_backend=uidrange` |
| 23:09:47.302 | `DisconnectJump completed=true` |

仅 **5.78 秒**（点完第二个「中断」立即恢复网络），随后在
`penalty-231024.png` 中出现「功能限制剩余时间 24 秒」。这是本次把
「等连接失败弹窗出现再恢复网络」写进流程的直接原因。

## 文件

| 文件 | 说明 |
| --- | --- |
| `autoend-log-excerpt.txt` | AutoEnd 时间线原始日志 |
| `autocontinue-log-excerpt.txt` | AutoContinue + 下一局完成日志 |
| `penalty-2309-log-excerpt.txt` | 提前恢复网络的 5.78 秒跳车日志 |
| `autoend-summary.json` / `autoend-checkpoints.jsonl` | AutoEnd 局录制摘要与 checkpoint |
| `autocontinue-summary.json` / `autocontinue-checkpoints.jsonl` | AutoContinue 局录制摘要与 checkpoint |
| `autoend-engine-incomplete.png` | AutoEnd 局引擎结束帧（生命归零） |
| `autoend-cleanup-completed.png` | AutoEnd 局触点清理完成帧 |
| `autocontinue-engine-incomplete.png` | AutoContinue 局引擎结束帧 |
| `autocontinue-cleanup-completed.png` | AutoContinue 局触点清理完成帧 |
| `penalty-231024.png` | 提前恢复网络后的「功能限制剩余时间 24 秒」截图 |

## 限制

- 仅 MuMu 12 真机验证；雷电的 iptables 回退仅有单测。
- 本证据不包含跳车后的主页截图；日志中的 `completed=true`、任务结束原因和
  无处罚截图共同证明流程完成。
