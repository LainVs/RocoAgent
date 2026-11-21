# -*- coding: utf-8 -*-
"""测试 windows-mcp 的 streamable-http 端点是否可用。

用法：
    python scripts\\test_mcp_http.py
"""
import json
import sys
import urllib.request


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    url = "http://127.0.0.1:8000/mcp"
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
    print("POST %s" % url)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            print("  状态: %s" % r.status)
            body = r.read().decode("utf-8", "ignore")
            print("  响应: %s" % body[:500])
            # 记录 session id（后续调用需要）
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                print("  Session-Id: %s" % sid)
            return 0
    except Exception as e:
        print("  失败: %s: %s" % (type(e).__name__, e))
        if hasattr(e, "read"):
            try:
                print("  响应体: %s" % e.read().decode("utf-8", "ignore")[:300])
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
