# setAccent 硬编码色值绕过了 CSS token

**版本**：`ui-m85`
**日期**：2026-11-21
**前置**：`ui-m84`

---

## 一、问题（实机 DOM 测量发现）

在真实悬浮窗里切主题，量 `--accent` 的实际值：

```
浅色  --accent = #FF6B4A
深色  --accent = #FF6B4A      <- 两个主题一样！
```

而我 `ui-m80` 改的是：

```
浅色  --accent = #884027
深色  --accent = #E2987F
```

**CSS 改了，但实际不生效。**

### 1.1 根因

`ui/app.js` 第 27-52 行：

```js
const ACCENT = {
  strong: '#FF6B4A',
  even:   '#7FD3E8',
  weak:   '#5B7FD4',
};

function setAccent(kind) {
  const c = ACCENT[kind] || ACCENT.even;
  root.style.setProperty('--accent', c);        // <- 内联样式，优先级最高
  root.style.setProperty('--accent-soft', hexA(c, 0.55));
  root.style.setProperty('--accent-dim', hexA(c, 0.16));
}
```

内联样式（`element.style`）优先级**高于**任何 CSS 规则，包括
`body.theme-dark { --accent: ... }`。所以 CSS token 永远被覆盖。

而且 `setAccent()` 在每次 `render()` 里都调用（第 397-400 行），
所以它一直在覆盖。

### 1.2 实测对比度

三个硬编码值按「整窗 alpha 0.78 × 三种游戏底色」算：

| 值 | 深色最坏 | 浅色最坏 | 需 4.5 |
|---|---|---|---|
| `strong` `#FF6B4A` | 3.79 | **2.23** | FAIL |
| `even` `#7FD3E8` | 5.74 | **1.47** | FAIL |
| `weak` `#5B7FD4` | 3.07 | **2.76** | FAIL |

**浅色主题全部不达 AA，最低 1.47** —— 几乎看不见。

`even` 在浅色下 1.47 特别危险：它是"互不克制"时的默认色，
而"互不克制"是最常见的对局状态。

---

## 二、决策

### 2.1 不移除 setAccent 的功能

`setAccent` 的设计意图是对的：**强调色由克制关系推导**
（克制=暖红 / 中性=青 / 被压=蓝）。这是悬浮窗的核心视觉语言。

问题只是**色值是硬编码的、且没考虑主题和对比度**。

### 2.2 改成「主题 × 克制关系」双维度 token

在 `style.css` 里定义六个 token：

```css
:root {
  /* 浅色主题：克制关系三态 */
  --accent-strong: #884027;   /* 克制：砖红 */
  --accent-even:   #1F6B7A;   /* 中性：深青 */
  --accent-weak:   #3A5A9E;   /* 被压：深蓝 */
}
body.theme-dark {
  --accent-strong: #E2987F;   /* 克制：暖珊瑚 */
  --accent-even:   #7FD3E8;   /* 中性：亮青 */
  --accent-weak:   #8FA8E8;   /* 被压：亮蓝 */
}
```

`setAccent()` 只切换**用哪个 token**，不再写具体色值：

```js
function setAccent(kind) {
  root.dataset.accent = kind;   // strong / even / weak
}
```

CSS 侧：

```css
/* --accent 跟随 dataset.accent 指向具体 token */
:root { --accent: var(--accent-strong); }
:root[data-accent="even"] { --accent: var(--accent-even); }
:root[data-accent="weak"] { --accent: var(--accent-weak); }
```

这样：
- 主题切换自动生效（深色/浅色各有一套）
- 对比度可以在 CSS 里单独校验
- 不再有内联样式覆盖

### 2.3 保留 JS 侧的兜底

如果 CSS 没定义（老版本），`setAccent` 仍写内联值。
但**内联值也要按主题选**：

```js
const ACCENT = {
  light: { strong: '#884027', even: '#1F6B7A', weak: '#3A5A9E' },
  dark:  { strong: '#E2987F', even: '#7FD3E8', weak: '#8FA8E8' },
};
```

---

## 三、实施

1. `ui/style.css`
   - 加六个 token（两主题 × 三态）
   - `--accent` 改成 `var(--accent-strong)` + `[data-accent]` 选择器
2. `ui/app.js`
   - `setAccent()` 改成设 `dataset.accent`
   - 保留内联兜底（按主题选值）
3. `src/test_ui.py`
   - `test_overlay_contrast` 扩展：校验六个 accent token
   - 新增：切主题后 `--accent` 实际值必须变（防止再被内联覆盖）
4. 实机验证

---

## 四、验证

- 浅色/深色 × 三态，六个组合全部 >= 4.5
- 切主题后 `getComputedStyle(root).getPropertyValue('--accent')` 必须变
- `setAccent('even')` 后 `--accent` == `--accent-even`
- `test_ui.py` 全过

---

## 五、不做

- **不移除「克制关系推导强调色」的设计**（那是核心视觉语言）
- **不改 `--accent-soft` / `--accent-dim` 的用法**（只是跟着变）
