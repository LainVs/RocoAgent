# 验证计划：技能识别链路（2026-11-23 03:xx）

## 前提：刚修好"悬浮窗遮挡 HUD"

实测证明悬浮窗盖住游戏左上角时 `my_pct=None` / `bar=False`，
导致 `_maybe_band_scan` 永不触发。悬浮窗已挪到右下角，读数恢复正常：
`my=98.0 / foe=12.7 / bar=True / npills=5 / skills=['240','162','150','20']`

## 待验证（进战斗后）

### A. 我方身份（ONNX 主通道）
**已有证据**（修复前就在工作）：
```
PET -> 恶魔狼 (ONNX 1.00)
PET -> 卡瓦重 (ONNX 1.00)
PET -> 音速犬 (ONNX 1.00)
```
✅ 都是 my_team 内的正确名字，不再是"银月狼王"。
**待确认**：冰钻布鲁斯能不能正确认出（用户明确说实机是它）。

### B. 技能名带 BAND
**预期日志**：`BAND -> <我方名> 槽N = <技能名> (conf)`
**当前状态**：历史上一次都没成功（conf 全 0.00 或未触发）。
**阻塞点已排除**（bar=False 问题），现在应该能触发。

若仍失败，可能原因（按顺序排查）：
1. `skill_pill_boxes` 在 4:3 下返回的 box 坐标错（基准 2560 的 SKILL_BOX
   按比例缩放到 2048 时偏移）
2. `read_band` 的 ROI 深度（+5~+95）在 4:3 下不对
3. OCR 对 4:3 缩放后的 2~3 字小图不敏感

### C. 播报 CAST
**预期日志**：`CAST -> 我方 <名字> 槽N = <技能名> (播报, conf)`
**要求**：出招后 1~6 秒内抓到播报条。

## 检查命令

```powershell
# 实时盯这三个模式
Get-Content logs\bot_stdout.log -Encoding UTF8 -Tail 40 -Wait |
  Select-String -Pattern "BAND ->|CAST -> 我方|PET ->"

# 看 session_moves 是否开始有内容
Get-Content data\session_moves.json -Encoding UTF8

# turns.jsonl 的 skill_source 分布
Select-String -Path logs\turns.jsonl -Pattern "skill_source" | Select-Object -Last 5
```

## 版本管理

如发现问题 -> 修复 -> 提交（每个问题单独提交，写清实测数据）。
本次验证的结论写入 `docs/ROUND2_RESULT.md` 的"实盘验证"一节。