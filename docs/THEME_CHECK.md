# 双主题配色核对（2026-11-21）

> 配色改动 `ui-m80`（token 对比度）+ `ui-m85`（setAccent 绕过 CSS）
> 的实机核对记录。截图见文末说明。

---

## 一、核对方法

用 Chrome headless 渲染**真实的** `ui/index.html` + `ui/style.css`（内联），
注入一帧完整战斗数据（银月狼王 vs 怖哭菇，含技能卡 / 结论 / 替补），
两个主题各截一张。

**不用 PrintWindow** —— WebView2 上它抓的是缓存帧（见 `FRONTEND.md` §8.6.1）。

**不用真实悬浮窗截图** —— 这轮环境不稳（keepalive 抢着拉起新进程，
窗口一直没显示出来）。DOM 测量（`theme_check.py`，独立 webview）已经
验证了实际渲染值，截图只做视觉确认。

---

## 二、深色主题

底色 `#1F1E1D`（暖深灰，不是纯黑），主文字 `#F5F4EE` 暖白，
accent `#E2987F` 暖珊瑚。

肉眼核对：

| 元素 | 观察 |
|---|---|
| 「性格 —」「物防 150~190」 | 清晰可读（改前 2.80:1 几乎看不见） |
| 「被克制 ×2.0」 | accent 色，醒目但不刺眼 |
| 技能卡伤害条 | 58% / 44% / 22% 层次分明 |
| 结论「2 回合击杀 · 5 回合被击杀 · 先手」 | 清楚 |

## 三、浅色主题

底色 `#FAF9F5` 暖白，主文字 `#1F1E1D`，accent `#884027` 砖红。

同样清晰。两个主题都保持了 Claude 那种暖调，不是灰扑扑的。

---

## 四、截图脚本的一个局限（不影响结论）

截图脚本里 `.wrap { opacity: 0.78 }` 没生效 —— 因为 `.overlay` 是
`position: fixed`，脱离了 `.wrap` 的层叠上下文。

但**不影响结论**：

- alpha 0.78 下文字和背景**一起**变淡，对比度不变
  （见 `FRONTEND.md` §8.2.1 的模型推导）
- 截图验证的是「纯色下的可读性」，正是 `test_overlay_contrast`
  里「不透明」那一列的数字
- 真正需要 alpha 合成的部分，已由 `test_overlay_contrast`
  （24 项，三种游戏底色取最坏值）覆盖

---

## 五、实际渲染值（DOM 测量）

`theme_check.py` 在独立 webview 里量**实际渲染出的文字颜色**
（不是 token 声明值）：

```
light  muIn "×0.5"  color = rgb(136, 64, 39)    <- #884027
dark   muIn "×0.5"  color = rgb(226, 152, 127)  <- #E2987F
```

改前两个主题都是 `#FF6B4A`（JS 硬编码覆盖了 CSS token）。
现在主题切换真的生效了。

---

## 六、最终对比度（整窗 alpha 0.78 × 三种游戏底色取最坏）

| token | 深色 | 浅色 | 门槛 |
|---|---|---|---|
| `--ink-0` | 8.15 | 8.46 | 4.5 |
| `--ink-2` | 5.03 | 4.50 | 4.5 |
| `--ink-3` | 4.51 | 4.50 | 4.5 |
| `--accent-strong` | 4.52 | 4.51 | 4.5 |
| `--accent-even` | 5.74 | 4.51 | 4.5 |
| `--accent-weak` | 4.53 | 4.53 | 4.5 |
| `--win` | 4.51 | 4.51 | 4.5 |
| `--warn` | 4.50 | 4.52 | 4.5 |
| `--kill-text` | 4.51 | 4.52 | 4.5 |
| `--kill`（图形） | 3.00 | 3.63 | 3.0 |

全部达标。`test_ui.py` 里 24 项断言锁住这些数字。

---

## 七、截图

截图在 `%TEMP%\opencode\theme_light.png` / `theme_dark.png`，
**没有入库**（`docs/shots/` 在 `.gitignore` 里，避免仓库膨胀）。

要重新生成：

```powershell
# 用临时脚本（不在仓库里）
python %TEMP%\opencode\gen_theme_shots.py
```

脚本做的事：读真实 `ui/index.html` + `ui/style.css` → 内联 CSS →
注入战斗数据 → Chrome headless 截图。

---

## 八、方法论教训

`ui-m80` 改完 CSS 就直接说「对比度修好了」，**没验证实际渲染值**。

如果不是这轮做主题实机核对，`setAccent` 那个 bug 会一直留着 ——
而且它会**掩盖** `ui-m80` 的修复（CSS 是对的，但被 JS 内联样式覆盖）。

**教训：改 token 类的东西，必须量 `getComputedStyle` 的实际值，
不能只看源码里写了什么。**
