# Security Events 模块架构

## 概述

Security Events 是 agent-sec-core 的安全事件持久化和查询子系统。采用 fire-and-forget 双写模型（JSONL + SQLite），记录所有安全能力的执行结果，支持按事件类型、时间、会话、verdict 等维度高效查询。

## 架构组件

```
security_events/
├── __init__.py           # 公共 API（log_event, get_writer, get_reader）
├── schema.py             # SecurityEvent 数据类定义
├── writer.py             # JSONL 文件追加写入器
├── sqlite_writer.py      # SQLite 事件写入器
├── sqlite_reader.py      # SQLite 事件查询器
├── models.py             # SQLAlchemy ORM（SecurityEventRecord + schema 迁移）
├── orm_base.py           # ORM Base 声明
├── orm_store.py          # ORM 模型注册 + schema convergence
├── repositories.py       # 高层仓储（分页、聚合、报告级查询）
├── config.py             # 存储路径配置
├── schema_version.py     # Schema 版本常量
├── sqlite_maintenance.py # SQLite 维护（vacuum、WAL checkpoint）
└── summary_formatter.py  # 安全事件摘要格式化器
```

## 核心数据流

```
安全能力执行完成
      │
      ▼
lifecycle.post_action() / on_error()
      │
      ├─ 构造 SecurityEvent (event_type, category, result, details, trace_id, ...)
      │
      ▼
  log_event(event)
      │
      ├─ JSONL Writer → ~/.agent-sec/security_events.jsonl（追加写）
      └─ SQLite Writer → ~/.agent-sec/security_events.db（索引写入）
      │
      ▼
  查询侧
      ├─ get_reader() → SqliteEventReader（按条件查询）
      ├─ repositories.py → 聚合报告
      └─ summary_formatter.py → 人类可读摘要
```

## 关键设计

### Fire-and-Forget 双写

- `log_event()` 调用不会因任何写入失败而抛出异常
- JSONL 和 SQLite 写入互相独立，任一失败不影响另一通道
- 调用方永远不会因事件记录失败而被中断

### SecurityEvent 模型

核心字段：
- `event_id`: UUID，唯一标识
- `event_type`: 对应 middleware action（如 `code_scan`, `sandbox_prehook`）
- `category`: 事件分类（如 `code_scan`, `sandbox`, `prompt_scan`）
- `result`: `succeeded` / `failed`
- `verdict`: 从 details 中提取的安全判定（pass/warn/deny）
- `details`: JSON 格式的完整请求+响应数据
- 关联 ID：`trace_id`, `session_id`, `run_id`, `call_id`, `tool_call_id`
- 时间：`timestamp`（ISO-8601）+ `timestamp_epoch`（浮点秒）

### SQLite Schema 与迁移

- ORM 模型定义复合索引：event_type, category+epoch, trace_id, session_id+epoch, verdict+epoch
- `schema_version.py` 管理 schema 版本号
- `migrate_security_events_schema()` 支持增量迁移（如 verdict 列的批量回填）
- 批量迁移使用固定 batch size（5000）+ 逐批 commit，避免长时间写锁

### 摘要格式化

- `summary_formatter.py` 将事件列表转为结构化安全摘要
- 支持按 session/run 粒度汇总 verdict 分布

## 对外接口

### 公共 API

```python
def log_event(event: SecurityEvent) -> None
def get_writer() -> SecurityEventWriter
def get_sqlite_writer() -> SqliteEventWriter
def get_reader() -> SqliteEventReader
```

### 被调用方

- `security_middleware/lifecycle.py` — 每次 invoke 完成后自动记录
- `telemetry` 模块 — 派生遥测指标
- CLI 子命令 — 查询和报告
