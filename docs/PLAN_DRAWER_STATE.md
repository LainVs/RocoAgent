# 设置抽屉显示真实状态（折叠 / 透明度 / 穿透）

**版本**：`ui-m81`
**日期**：2026-11-21
**前置**：`ui-m80`

---

## 一、问题

### 1.1 折叠状态有两份存储，互相不知道

```
前端  localStorage['overlay.folded']
原生  config/overlay.json 的 "folded"
```

`bindFold()` 启动时**只读 localStorage**：

```js
if (localStorage.getItem('overlay.folded') === '1') toggle();
```

而 `enter_embedded()` 读的是 `state["folded"]`（来自 json）。
两处不一致时，行为取决于哪个先跑。

**实测后果**（`ui-m75` 排查时）：

```
config/overlay.json: {"folded": true}
悬浮窗实际高度:      306（= 153 CSS，折叠态）
日志里:              完全没有"折叠"字样
```

我为此排查了半小时，以为是 flex 布局 / 测量函数的问题。

### 1.2 原生侧有 `status()`，前端从不调用

`src/overlay.py` 的 `Api.status()` 返回：

```python
{
  "opacity": 230,
  "folded": False,
  "dom": {...},       # 页面自报的 DOM 状态
  "win": {...},       # 窗口矩形
}
```

这是**唯一可信的真相来源**（原生侧才是真正决定窗口行为的地方），
但前端一个字段都没用。

### 1.3 设置抽屉里的按钮不反映状态

现在抽屉打开看到的是：

```
透明度  [====|====]  90%
主题    [浅色][深色]
[点击穿透] [折叠]
```

**看不出**：现在到底是不是折叠态、穿透开没开。

用户（和我）只能靠"窗口高度"去猜。

---

## 二、决策

### 2.1 抽屉里加一行「状态」，从原生读

```
状态    折叠中 · 穿透关闭 · 透明度 90%
```

数据来源：`callNative('status')`。

**为什么不在前端自己记**：前端只知道"我点了折叠"，不知道
"原生侧认不认"。实测出现过前端 `folded=true` 但窗口没变的情况
（`set_folded` 调用失败、或 `fit_window` 覆盖）。

### 2.2 按钮要反映当前状态

- `折叠` / `展开` —— 已有（`btnFold.textContent`）
- `点击穿透` —— 已有 `aria-pressed`，但**没有视觉反馈**
- 加 `.tbtn[aria-pressed="true"]` 的样式

### 2.3 折叠状态单向同步：原生 -> 前端

**不改现有方向**（前端点按钮 -> `set_folded` -> 原生），
但**加一条反向同步**：原生 `enter_embedded` 恢复折叠态后，
前端要更新 `folded` 变量和按钮文案。

现在 `enter_embedded` 里是直接 `evaluate_js` 改 DOM：

```js
if (ov) ov.classList.add('folded');
if (btn) btn.textContent = '展开';
```

**问题**：它没更新前端模块级的 `folded` 变量。之后用户点一次按钮，
`folded = !folded` 从 `false` 变 `true`，但 UI 已经是折叠态 —— 变成"点了没反应"。

改成调用前端暴露的函数：`window.__setFolded(true)`。

---

## 三、实施

1. `ui/app.js`
   - 暴露 `window.__setFolded(v)` —— 统一入口，同时更新
     `folded` 变量 / class / 按钮文案 / localStorage
   - `bindFold()` 的 `toggle()` 改成调它
   - 新增 `refreshStatus()` —— 调 `callNative('status')`，更新抽屉里的状态行
   - 抽屉打开时 + 折叠切换后刷新
2. `ui/index.html`
   - 抽屉加一行 `<div class="drawer-row"><span class="dlabel">状态</span>
     <span id="dstate"></span></div>`
3. `ui/style.css`
   - `.tbtn[aria-pressed="true"]` 的视觉反馈
   - `#dstate` 的样式
4. `src/overlay.py`（我的文件）
   - `enter_embedded` 里改成 `window.__setFolded(true)` 而不是直接改 DOM
5. `src/test_ui.py` 加用例

---

## 四、验证

- 抽屉打开能看到「折叠中 / 展开 · 穿透关闭 · 透明度 90%」
- 点折叠按钮，状态行立刻变
- 重启后从 json 恢复折叠态，前端 `folded` 变量同步（不是只改 DOM）
- 穿透按钮有视觉反馈
- `test_ui.py` 全过

---

## 五、不做

- **不合并两份存储**（那是结构性改动，风险高；先做到"状态可见"）
- **不改 `set_folded` 的调用方向**
- **不加新热键**
