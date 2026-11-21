# test_ui.py 拆分：悬浮窗 / 控制台分离

**版本**：`ui-m83`
**日期**：2026-11-21
**前置**：`ui-m82`

---

## 一、背景

三个会话都在改 `src/test_ui.py`（1642 行、170 项）：

```
[ui-overlay]  我          悬浮窗 DOM / 配色 / 窗口状态
[ui-session] 控制台会话   API 契约 / 静态资源 / 日志尾推断 / 图鉴去重
[主会话]                 偶尔会动（standalone 相关断言）
```

控制台会话提议拆分（`logs/PEER_MESSAGE.md` §五之二），**并说「必须他先同意」** ——
指我。本文件是正式回应。

---

## 二、实测的分组结构

```
  行号  分组                归属
─────────────────────────────────────────
   88  [1] API              控制台
  218  [2] 静态资源          控制台
  255  [3] 契约              控制台
  287  [4] DOM               悬浮窗  ← 含 DOM_WORKER
  458  [4.5] 标注            控制台
  585  [5] 窗口状态          悬浮窗
  646  [6] 韧性              悬浮窗  ← 含 RESILIENCE_WORKER
 1158  [3b4] fit 状态同步    悬浮窗（我加的）
 1203  [3c] 对比度（控制台）  控制台
 1266  [3c2] 悬浮窗对比度    悬浮窗（我加的）
 1376  [3b] 日志尾推断       控制台
```

## 三、依赖分析（决定拆分方式）

`test_ui.py` 里的函数依赖只有三层：

```
共享层（两边都要）
    ROOT / SRC / UI          路径常量
    FAILS / PASSES           计数器
    ok() / port_free() / wait_health() / get() / get_raw()

控制台层
    test_api / test_static / test_contract / test_label
    test_contrast / test_phase_infer

悬浮窗层
    DOM_WORKER / test_dom
    test_window_state
    RESILIENCE_WORKER / test_resilience
    test_fit_state_sync / test_overlay_contrast
```

**关键发现**：`test_fit_state_sync` 和 `test_overlay_contrast` 是我加的，
它们放在 `[3b]` / `[3c]` 附近（文件中间），但**归属悬浮窗** ——
按分组标记切会切错。必须按**函数归属**切，不是按位置。

## 四、拆分方案

```
src/test_ui_common.py    新建  共享层（约 90 行）
src/test_ui.py           保留  悬浮窗（约 700 行，6 组）
src/test_console.py      新建  控制台（约 800 行，7 组）
```

### 4.1 `test_ui_common.py`

```python
ROOT / SRC / UI
FAILS / PASSES
ok(cond, msg, detail="")
port_free(port)
wait_health(port, timeout=12)
get(port, path, timeout=6)
get_raw(port, path, timeout=6)
summary() -> int        # 打印汇总并返回退出码
```

`summary()` 是新增的 —— 现在每个文件各自 `print("全部通过（N 项）")`，
抽出来避免两边格式不一致。

### 4.2 `test_ui.py`（悬浮窗）

```
[4]   DOM 渲染
[4.5] 标注页（控制台会话说归他，但它依赖 DOM 渲染基础设施）
[5]   窗口状态持久化
[6]   韧性
[3b4] fit 状态同步
[3c2] 悬浮窗对比度
```

`[4.5] 标注页` 有争议：它测的是 `console.html` 的标注功能（控制台），
但用的是悬浮窗的 DOM 测试基础设施（`DOM_WORKER`）。

**决定**：留在 `test_ui.py`，因为它复用 `DOM_WORKER`；
但注释里写明"归属控制台，暂放这里因为共享 worker"。

### 4.3 `test_console.py`（控制台）

```
[1]   API 契约
[2]   静态资源
[3]   live_state 契约
[3b]  日志尾推断
[3c]  控制台对比度
```

---

## 五、实施步骤（分两次提交，降低风险）

### 第一次（`ui-m83`）：抽共享层，不改任何测试逻辑

1. 新建 `test_ui_common.py`，把 `ok/port_free/wait_health/get/get_raw`
   和 `ROOT/SRC/UI/FAILS/PASSES` 搬过去
2. `test_ui.py` 改成 `from test_ui_common import *`
3. **跑测试确认 170 项仍然全过**（这一步不改变任何断言）
4. 提交

**为什么要分步**：如果一步到位（抽层 + 拆分），出问题时不知道是哪一步坏的。

### 第二次（`ui-m84`）：真正拆出 `test_console.py`

1. 把 `test_api` / `test_static` / `test_contract` / `test_contrast` /
   `test_phase_infer` 搬到 `test_console.py`
2. `test_ui.py` 的 `main()` 去掉这些调用
3. 两个文件都能独立跑
4. 提交

**这次要控制台会话确认** —— 因为搬的是他的代码。

---

## 六、验证

- 拆分后 `test_ui.py` 单独跑：悬浮窗那 6 组全过
- 拆分后 `test_console.py` 单独跑：控制台那 5 组全过
- 两边项数加起来 == 拆分前的 170
- 两个文件都 exit=0

---

## 七、不做

- **不动任何断言内容**（只搬位置）
- **不改测试覆盖范围**
- **不在同一次提交里做"抽层 + 拆分"**（分两步）
