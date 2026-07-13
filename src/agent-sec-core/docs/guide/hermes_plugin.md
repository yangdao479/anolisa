---
sources:
  - design/hermes_plugin
  - design/skill_ledger
  - design/code_scanner
  - design/pii_checker
---

# Hermes Plugin 使用指南

## 功能概述

Hermes Plugin 为 Hermes Agent 运行时提供完整的安全防护。通过 Hermes 插件框架自动加载，在 Agent 生命周期各阶段执行安全检测，所有能力可通过配置文件独立启用/禁用。

## 安装

### 通过适配器安装

```bash
# 使用 ANOLISA 适配器
adapters/hermes/scripts/install.sh
```

### 手动安装

将 `hermes-plugin/` 目录部署到 Hermes 插件路径：
```
~/.hermes/plugins/agent-sec-core-hermes-plugin/
```

## 安全能力

| 能力 | ID | 触发点 | 功能 |
|------|-----|--------|------|
| 代码扫描 | `code-scan` | PreToolUse (Bash) | 检测危险代码 |
| Prompt 检测 | `prompt-scan-user-input` | UserPromptSubmit | 检测 Prompt 注入 |
| PII 检测 | `pii-scan-user-input` | UserPromptSubmit + PostToolUse | 检测敏感信息 |
| Skill 完整性 | `skill-ledger` | PreToolUse (skill) | 校验 Skill 合法性 |
| 可观测性 | `observability` | 全生命周期 | 采集运行指标 |

## 配置

配置文件位于插件目录下 `src/config.toml`：

```toml
[capabilities.code-scan]
enabled = true
timeout = 10
enable_block = false    # true 时检测到问题直接拦截

[capabilities.observability]
enabled = true
timeout = 5

[capabilities.pii-scan-user-input]
enabled = true
timeout = 10
include_low_confidence = false
warning_ttl_seconds = 300

[capabilities.prompt-scan-user-input]
enabled = true
timeout = 15
warning_ttl_seconds = 300

[capabilities.skill-ledger]
enabled = true
timeout = 5
policy = "ask"          # ask/debug/warn/block
enable_block = false    # Deprecated, 使用 policy 替代
```

### 配置说明

- **enabled**：是否启用该能力（false 则完全跳过）
- **timeout**：hook 执行超时（秒）
- **policy**（skill-ledger）：`ask` 优先确认 / `debug` 仅日志 / `warn` 告警放行 / `block` 拦截
- **warning_ttl_seconds**：同一告警的去重窗口

## 行为特性

### Fail-Open 设计

- 插件加载失败不影响 Hermes 正常运行
- 配置缺失/错误时跳过对应能力（而非崩溃）
- 单个 hook 异常不阻断 Agent 流程

### 性能监控

- hook 执行超过 2 秒发出 slow hook 警告
- 所有 hook 回调包裹 try/except，异常仅记录日志

## 卸载

```bash
adapters/hermes/scripts/uninstall.sh
```
