# 计划：修复机器人静默停止（botsup 卡在 wait）

## 现象（2026-11-21 08:28）

```
bot_stdout.log 停在 08:28:23
supervisor_restarts.log 最后一条是 08:23:01 starting bot (run 1)
之后 5 分钟无任何新记录
```

同时存在：
```
botsup 470252 -> robot 477052（僵死）
botsup 479888 -> robot 478172（僵死）
robot.lock 被僵死进程持有
```

新实例启动时 `acquire_lock()` 返回 False -> 退出（rc=3）。

## 根因

`D:\Tools\botsup.py` 的守护逻辑有三个缺口：

### 缺口 1：没有"进程存在性"检查

```python
while True:
    try:
        rc = p.wait(timeout=5)      # 一直等
        break
    except subprocess.TimeoutExpired:
        ...
```

如果 child **僵死但未退出**（Windows 上进程句柄还在），`p.wait()` 永远不返回。
脚本卡在这里，既不重启也不报错。

### 缺口 2：卡死检测阈值太长

```python
if time.time() - t0 > 90:              # 启动 90 秒后才开始检查
    age = time.time() - os.path.getmtime(LOG)
    if age > 240:                       # 4 分钟无日志才杀
```

实测日志停在 08:28:23，到 08:33 还没动作 —— 要等 4 分钟。
而用户看到的是"机器人不动了"。

### 缺口 3：多个 botsup 可以共存

没有任何互斥，`keepalive.py` 拉一个、手工拉一个，就有两个 botsup。
两个 botsup 各拉一个 robot，第二个因 lock 失败退出（rc=3），
第一个的 child 僵死，于是两个 botsup 都卡住。

## 修复方案

### A. 改 `D:\Tools\botsup.py`（外部文件，需谨慎）

1. **用 `p.poll()` 轮询代替阻塞 `p.wait()`**，并加**心跳检查**：
   ```python
   while True:
       rc = p.poll()
       if rc is not None:
           break
       age = time.time() - os.path.getmtime(LOG)
       if age > STALL_S:                # 默认 120 秒
           kill_and_restart()
           break
       time.sleep(2)
   ```

2. **降低卡死阈值**：`age > 240` -> `age > 120`（机器人每 15 秒写 state，
   120 秒无日志必然是卡了）。

3. **启动时清理自己的旧实例**：写 pidfile + 存活检查，
   已有 botsup 在跑就直接退出。

### B. 项目内加一层保护（`src/keepalive.py` 增强）

现有 keepalive 只检查 botsup 是否存在，不够。增加：

1. **检查 bot_stdout.log 的新鲜度**：超过 180 秒没更新 -> 强制重启整条链
   （杀 botsup + robot，再拉起）。
2. **清理僵死进程**：按 PID（不是按名字）杀掉不响应的 robot。
3. **单实例锁**：已经有，但要确认在拉起前检查。

### C. 更根本的：robot 自己要能退出

`robot.lock` 被僵死进程持有是致命的。加一个**看门狗线程**：
主循环超过 N 秒没进展就 `os._exit(1)`，让 OS 释放锁。

## 实施顺序

1. **先做 C**（项目内，可控）：robot 自检卡死并主动退出
2. **再做 B**（项目内，可控）：keepalive 检查日志新鲜度
3. **最后考虑 A**（外部文件，需要用户同意）

## 验收标准

1. 人为让 robot 卡死（挂起进程），180 秒内自动恢复
2. 日志里出现明确的 "STALL detected" 记录
3. 不再出现两个 botsup 各拉一个 robot 的情况

## 不做的事

- 不按进程名批量杀 python（会连带 ui_server）
- 不改 robot 的决策逻辑（这次只修存活）
