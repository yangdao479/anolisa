# Observability 模块架构

## 概述

Observability 模块负责采集和持久化 AI Agent 运行时的可观测性数据。记录各 hook 生命周期点的指标（如 token 用量、延迟、模型调用等），支持 JSONL 和 SQLite 双写存储，提供会话级汇总报告和关联查询能力。

## 架构组件

```
observability/
├── __init__.py          # 公共 API（record_observability, get_writer, get_sqlite_writer）
├── schema.py            # Pydantic 模型（ObservabilityRecord, ObservabilityMetadata, hooks）
├── models.py            # SQLAlchemy ORM 模型（ObservabilityEventRecord）
├── writer.py            # JSONL 文件写入器
├── sqlite_writer.py     # SQLite 写入器
├── sqlite_reader.py     # SQLite 查询器
├── repositories.py      # 高层仓储接口（聚合查询、分页）
├── correlation.py       # 关联上下文管理（session/run/call 级别）
├── review.py            # 会话回顾/报告生成
├── session_report.py    # 会话报告格式化
├── metrics.py           # 指标白名单（HOOK_METRIC_ALLOWLIST）
├── config.py            # 配置（存储路径等）
└── cli.py               # CLI 子命令（observability 查询）
```

## 核心数据流

```
Hook 执行点 (PreToolUse / AfterModel / PostToolUse / ...)
        │
        ├─ 构造 ObservabilityRecord (hook, metadata, metrics, observedAt)
        │
        ▼
  record_observability(record)
        │
        ├─ JSONL Writer → ~/.agent-sec/observability.jsonl
        └─ SQLite Writer → ~/.agent-sec/observability.db
        │
        ▼
  查询/报告
        ├─ CLI: agent-sec-cli observability ...
        ├─ repositories.py: 按 session/run 聚合
        └─ review.py / session_report.py: 会话回顾
```

## 关键设计

### 记录模型（ObservabilityRecord）

- `hook`: 生命周期点名称（如 `PreToolUse`, `AfterModel`, `PostToolUse`）
- `metadata`: 关联上下文（session_id, run_id, call_id, tool_call_id）
- `metrics`: 键值对指标集，仅白名单内的指标名会被持久化
- `observedAt`: 带时区的 ISO-8601 时间戳

### 指标白名单

- 通过 `HOOK_METRIC_ALLOWLIST` 控制可记录的指标名集合
- 防止任意字段膨胀存储和索引

### 关联上下文（Correlation）

- `ObservabilityMetadata` 基类：session_id + run_id（必填）
- `ModelCallMetadata` 扩展：+ call_id
- `ToolCallMetadata` 扩展：+ tool_call_id
- 所有 ID 经 `truncate_correlation_id()` 截断，保证存储一致性

### 双写存储

- **JSONL**：追加写，无 schema 迁移负担，适合流式消费
- **SQLite**：索引查询，支持按时间/session/run 分组聚合

### 会话报告

- `review.py`：对一个完整会话生成安全态势回顾
- `session_report.py`：格式化输出供 CLI 或 UI 展示

## 对外接口

### 公共 API

```python
def record_observability(record: ObservabilityRecord) -> None
def get_writer() -> ObservabilityWriter
def get_sqlite_writer() -> ObservabilitySqliteWriter
```

### 被调用方

- Hook 脚本 `observability_hook.py`（cosh/hermes/openclaw）— 每个生命周期点触发
- CLI `agent-sec-cli observability` — 查询和报告
- `security_middleware` lifecycle — 通过 telemetry 桥接
