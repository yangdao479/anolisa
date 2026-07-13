# Daemon 模块架构

## 概述

Daemon 模块实现了 agent-sec-core 的后台常驻服务。通过 Unix Domain Socket 暴露 NDJSON-RPC 接口，为 Agent 运行时提供低延迟的安全服务（prompt scan、security query、skill ledger 通知等），避免每次调用都启动新进程。

## 架构组件

```
daemon/
├── server.py                 # AsyncIO Unix socket 服务器主体
├── protocol.py               # NDJSON 帧协议（Request/Response 序列化）
├── gateway.py                # 请求预处理网关（鉴权、限流、日志）
├── registry.py               # Method 注册表（method name → handler）
├── runtime.py                # 运行时管理（socket 路径、lock、目录）
├── client.py                 # 客户端 SDK（同步连接 daemon）
├── health.py                 # 健康检查 handler
├── validation.py             # 请求参数校验
├── request_context.py        # 请求级上下文
├── env.py                    # 环境变量
├── errors.py                 # 错误类型层级
├── logging.py                # Daemon 日志设置
├── skill_ledger_activation.py # SkillFS 挂载通知 handler
├── handlers/
│   ├── prompt_scan.py        # prompt_scan RPC handler
│   └── security_query.py    # security_query RPC handler
└── jobs/
    └── registry.py           # 后台定时任务注册
```

## 核心数据流

```
Agent 运行时 / Hook 脚本
        │
        ▼
  Unix Socket 连接（~/.agent-sec/daemon.sock）
        │
        ▼
  NDJSONFrameParser.feed() → DaemonRequest
        │
        ▼
  DaemonGateway (鉴权 + 限流 + trace context)
        │
        ▼
  MethodRegistry.dispatch(method) → Handler
        │
        ├─ "health.ping"       → health handler
        ├─ "prompt_scan"       → prompt_scan handler → security_middleware.invoke()
        ├─ "security_query"    → security_query handler → security_events 查询
        └─ "skillfs_notify"    → skill_ledger_activation handler
        │
        ▼
  DaemonResponse → serialize → 写回 socket
```

## 关键设计

### 单实例保证

- `SingleInstanceLock`：基于 `fcntl` 文件锁，同一 runtime 目录只能有一个 daemon 实例
- socket 文件权限 `0o600`，仅当前用户可连接

### NDJSON 帧协议

- 请求格式：`{"method": "...", "params": {...}, "trace_context": {...}, "timeout_ms": N}`
- 响应格式：`{"request_id": "...", "ok": bool, "data": {...}, "error": {...}}`
- 帧边界：换行符分割
- 大小限制：默认 4MB 请求 / 4MB 响应

### 连接管理

- 最大并发连接：64（DEFAULT_MAX_CONNECTIONS）
- 请求读取超时：5s（DEFAULT_REQUEST_READ_TIMEOUT_MS）
- 优雅关闭：drain timeout 2s

### Method 注册

- 启动时通过 `create_default_registry()` 注册所有 method
- 当前注册：`health.*`, `prompt_scan`, `security_query`, `skillfs_notify`
- 每个 handler 接收解析后的 DaemonRequest，返回 DaemonResponse

### 错误处理

分层错误类型：
- `DaemonAlreadyRunningError` — 实例冲突
- `BadRequestError` — 请求格式错误
- `BusyError` — 连接数已满
- `DaemonTimeoutError` — 请求超时
- `ResponseTooLargeError` — 响应超限
- `ShutdownError` — 服务关闭中

## 对外接口

### 服务端

```bash
agent-sec-cli daemon start  # 启动 daemon
```

### 客户端 API

```python
from agent_sec_cli.daemon.client import daemon_health_reachable
# 通过 Unix socket 发送 NDJSON 请求
```

### 被调用方

- Hook 脚本可通过 daemon client 低延迟调用 prompt_scan 等服务
- systemd unit 管理 daemon 生命周期
