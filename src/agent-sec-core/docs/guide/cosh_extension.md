---
sources:
  - design/cosh_extension
  - design/sandbox
  - design/skill_ledger
  - design/code_scanner
  - design/pii_checker
---

# Cosh Extension 使用指南

## 功能概述

Cosh Extension 为 Cosh Agent 运行时提供最完整的安全防护，涵盖代码扫描、Prompt 注入检测、PII 检查、沙箱隔离执行、Skill 完整性校验和全生命周期可观测性。通过声明式 JSON 配置注册，覆盖 7 个 hook 生命周期点。

## 安装

### 系统安装

通过 ANOLISA 适配器部署到系统路径：
```
/usr/share/anolisa/extensions/agent-sec-core/
```

### 开发模式

直接将 Cosh 指向源码目录：
```bash
# 在 Cosh 配置中指定 extensionPath
```

## 安全能力

### 完整覆盖矩阵

| 能力 | PreToolUse | UserPromptSubmit | BeforeModel | AfterModel | PostToolUse | PostToolUseFailure | Stop |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Code Scanner | shell | — | — | — | — | — | — |
| Sandbox Guard | shell | — | — | — | — | — | — |
| Prompt Scanner | — | yes | — | — | — | — | — |
| PII Checker | all | yes | — | yes | yes | yes | — |
| Skill Ledger | skill | — | — | — | — | — | — |
| Observability | all | yes | yes | yes | yes | yes | yes |
| Sandbox Failure | — | — | — | — | — | shell | — |

### 能力说明

- **Code Scanner**：对 shell 命令的代码进行安全扫描
- **Sandbox Guard**：命令分类 + 策略生成 + linux-sandbox 隔离执行
- **Prompt Scanner**：检测用户输入中的 Prompt 注入/越狱
- **PII Checker**：多阶段 PII/凭据检测（用户输入、模型输出、工具输出）
- **Skill Ledger**：校验 Skill 文件完整性和合法性
- **Observability**：全生命周期运行指标采集
- **Sandbox Failure Handler**：沙箱失败时提供用户友好错误信息

## 配置

Cosh Extension 通过 `cosh-extension.json` 声明式配置，所有 hook 默认启用。各能力的行为通过环境变量控制：

```bash
export CODE_SCANNER_MODE=deny      # observe（默认）/ deny
export PROMPT_SCANNER_MODE=deny
export SKILL_LEDGER_MODE=deny
export PII_CHECKER_MODE=deny
```

### Hook 超时配置

在 `cosh-extension.json` 中各 hook 有独立超时：
- 代码扫描 / Skill 校验 / 可观测性：5000ms
- Prompt 检测 / PII 检测：10000ms
- Sandbox Guard：无显式超时（由沙箱执行决定）

## 沙箱隔离（Cosh 独有）

Cosh Extension 是唯一完整集成 sandbox-guard 的扩展：

1. **PreToolUse** 阶段：`sandbox-guard.py` 拦截 shell 命令
2. 执行四层分类（destructive → deny，其他 → 沙箱策略）
3. 调用 `linux-sandbox` 在隔离环境中执行命令
4. **PostToolUseFailure** 阶段：`sandbox-failure-handler.py` 处理沙箱错误

### 沙箱失败场景

当命令因沙箱限制失败时（如网络被禁止、路径不可写），failure handler 会生成用户友好的错误说明，帮助 Agent 理解失败原因并调整行为。

## 前置条件

1. Cosh Agent 运行时
2. `agent-sec-cli` 已安装且在 PATH 中
3. `linux-sandbox` 二进制可用（Linux 环境，用于沙箱功能）

## 版本

当前版本：0.7.0（与 agent-sec-core 同步）
