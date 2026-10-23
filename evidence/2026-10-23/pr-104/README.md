# LIVE BOOST 面板关闭失败证据（2026-10-23 22:40）

对应 PR：#104。

## 现象

- 配置 2，MuMu 12，竞演
- 22:40:17.880 `LiveBoostGate current=1 target=1 corrected=false verified=true`
  —— 面板已被打开并验证过
- 22:40:54.219 `RuntimeError：体力消耗页面或唯一选中状态未能连续稳定确认`
  —— 点「OK」后 36 秒仍停在体力消耗页面

调用栈：

```
ensure_live_boost(context)
  -> LiveBoostGate.ensure(target)
     -> self.stable(False)
        -> raise RuntimeError("体力消耗页面或唯一选中状态未能连续稳定确认")
```

`stable(False)` 是旧实现「点一次 OK 后直接等待页面稳定」的路径。修复后
`close_panel()` 会按面板是否已消失重试最多 3 次，仍未关闭才报错。

## 验证

- 单测：`pytest tests/test_live_boost_gate.py -q` → 17 passed
- 修复部署后 MuMu 12 竞演又跑 45+ 局，未再卡在体力消耗页
- 未正面复现「点击被吞」的瞬间，属于回归级证据

## 文件

| 文件 | 说明 |
| --- | --- |
| `boost-close-log-excerpt.txt` | 22:40:17 成功验证 → 22:40:54 失败的原始日志与栈 |

## 限制

- 仅 MuMu 12 上观测；坐标 `(640,640)` 沿用 720p 标定，雷电未复测。
