# 悬浮窗折叠态缺陷修复计划（2026-11-23）

## 现象

用户截图：悬浮窗只有 **373×223**，只显示我方/对面血条，没有技能区、
没有设置抽屉；点右上角设置按钮**无反应**。

## 根因（代码定位）

1. `config/overlay.json` 持久化了 `"folded": true`
2. `ui/style.css` 折叠态规则：
   ```css
   .overlay.folded .skills,
   .overlay.folded .verdict,
   .overlay.folded .drawer { display: none; }
   ```
   —— **把设置抽屉也一起隐藏了**，所以 `btnSet` 的 `classList.toggle('open')`
   虽然生效，但父元素 `display:none`，永远看不见。
3. 折叠态 `width: 320px`，高度由内容撑开 → 只有血条 ≈ 223px。

## 设计缺陷

"折叠"的语义应该是**收起技能区/详情，保留头部和设置入口**，
而不是"把设置也藏起来"。当前实现让用户**无法自己展开**——
除了记住要按 Ctrl+Alt+F 之类的快捷键（而且不一定有）。

## 修复方案（三层，逐层可独立验证）

### 第一层：CSS 不再隐藏抽屉
```css
/* 折叠时只收起内容区，设置抽屉必须可用 */
.overlay.folded .skills,
.overlay.folded .verdict,
.overlay.folded .side .side-foot,
.overlay.folded .side.foe .infer { display: none; }
/* .drawer 不隐藏 */
```

### 第二层：点设置时自动展开
折叠态点设置 → 先 `setFolded(false)` 再开抽屉。
理由：折叠态窗口太窄（320px），抽屉里的滑块/按钮放不下，
强行在里面渲染会挤压变形。展开后再开抽屉最自然。

### 第三层：折叠态也要有可见的"展开"入口
现在只有设置抽屉里的 `btnFold`。折叠后抽屉打不开 → 死锁。
方案：折叠态在头部直接显示一个 `⤢` 展开按钮（CSS 控制显隐）。

## 验证

1. `config/overlay.json` 改 `folded:false` 后重启 → 应显示完整面板
2. 点设置 → 抽屉展开，能看到透明度/主题/折叠按钮
3. 点"折叠" → 只收起技能区，头部仍有展开按钮
4. 折叠态点设置 → 自动展开并打开抽屉

## 顺带（同批）

- `docs/LATENCY.md`：记录"UI 刷新慢"的三层根因（机器人 16s 写盘 /
  中文名 404 / 折叠态隐藏），避免重复排查
