# 计划：把 windows-mcp 从 stdio 改成常驻 HTTP 服务

## 问题（2026-11-20 17:16 起 MCP 完全不可用）

`opencode.log` 里的证据：

```
mcp connected  server=windows-desktop tools=16  connectionID=e6a8f156...
mcp connected  server=windows-desktop tools=16  connectionID=ab9d56ee...
mcp connected  server=windows-desktop tools=16  connectionID=e09d5e99...
mcp connected  server=windows-desktop tools=16  connectionID=fee59950...

mcp transport error  server=windows-desktop
  error="MCP server process exited with code 255: Starting MCP server 'windows-mcp' transport 'stdio'"
```

**4 个会话（不同 directory）各自 spawn 了一个 `windows-mcp.exe serve`，
都用 stdio 传输。** stdio 模式下每个客户端独占一个进程，
但多个进程同时抓屏/操作窗口会互相干扰，最终全部退出（exit 255）。

## 修复方案：改用常驻 HTTP 服务

`windows-mcp serve --transport streamable-http --port 8000`
可以起一个**长期驻留**的服务，多个会话共享同一个进程。

### 步骤

1. **停掉所有残留的 windows-mcp 进程**
2. **用 install 装成登录自启的常驻服务**（默认就是 streamable-http:8000）
3. **改 opencode 配置**：从 `type: local`（stdio）改成 `type: remote`（HTTP）
4. **重启 opencode 服务**让配置生效

### 风险

- 改 `~/.config/opencode/opencode.jsonc` 属于 OpenCode 监视目录，
  改完会触发配置热重载，**可能导致服务重启、当前会话中断**
- 必须提前告知用户

### 回滚

备份原配置为 `opencode.jsonc.bak_mcp_http_<时间戳>`，
出问题直接覆盖回来。

## 为什么这样更好

| | stdio（现在） | streamable-http（改后） |
|---|---|---|
| 进程数 | 每个会话一个 | 全局一个 |
| 多会话共存 | 互相抢，全部崩 | 共享，互不干扰 |
| 启动开销 | 每次连接启动一次 | 常驻，零启动 |
| 屏幕截图 | 慢（每次冷启） | 快 |

## 验收标准

1. `windows-mcp.exe` 只剩一个常驻进程
2. 端口 8000 可访问
3. 重启 opencode 后 MCP 工具可用（能截图）
4. 会话不再因 MCP 崩溃而中断
