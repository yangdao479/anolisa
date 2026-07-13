---
sources:
  - design/observability
---

# Observability 使用指南

## 功能概述

Observability 模块记录 AI Agent 运行过程中的可观测性指标，覆盖所有 hook 生命周期点。数据持久化到本地存储（JSONL + SQLite），支持会话级报告和关联查询，帮助理解 Agent 的安全态势和行为模式。

## 使用方式

### CLI 命令

```bash
# 手动写入 observability 记录（从 stdin 读取 JSON）
agent-sec-cli observability record --stdin
```

**输入 JSON 格式：**

```json
{
  "hook": "PreToolUse",
  "observedAt": "2024-01-01T00:00:00Z",
  "metadata": {
    "sessionId": "sess-abc123",
    "runId": "run-def456"
  },
  "metrics": {
    "tool_name": "shell",
    "duration_ms": 150
  }
}
```

### Hook 自动采集

Observability hook 在所有生命周期点自动触发，无需手动调用：

| Hook 点 | 采集内容 |
|---------|---------|
| PreToolUse | 工具名、输入大小、时间戳 |
| UserPromptSubmit | 用户输入长度、时间戳 |
| BeforeModel | 模型调用开始时间 |
| AfterModel | 模型响应延迟、token 用量 |
| PostToolUse | 工具输出大小、执行耗时 |
| PostToolUseFailure | 失败原因、错误类型 |
| Stop | 会话结束标记 |

## 数据存储

### 存储位置

- JSONL 日志：`~/.agent-sec/observability.jsonl`
- SQLite 索引：`~/.agent-sec/observability.db`

### 关联查询

所有记录通过关联 ID 串联：
- `session_id`：一次 Agent 会话
- `run_id`：一次 Agent 运行/轮次
- `call_id`：一次模型调用
- `tool_call_id`：一次工具调用

## 配置选项

### 各运行时启用方式

**Hermes（config.toml）：**
```toml
[capabilities.observability]
enabled = true
timeout = 5
```

**OpenClaw（openclaw.plugin.json）：**
```json
{
  "capabilities": {
    "observability": {
      "enabled": true
    }
  }
}
```

**Cosh（cosh-extension.json）：**
默认启用，在所有 hook 点注册 `observability_hook.py`。

## 会话报告

Observability 数据支持生成会话级安全态势报告，包含：
- 各检测能力的触发次数和 verdict 分布
- 工具使用频率和风险评分
- 会话时长和交互轮次统计

## 指标白名单

仅白名单中声明的指标名会被持久化，防止存储膨胀。新增指标类型需在 `HOOK_METRIC_ALLOWLIST` 中注册。
