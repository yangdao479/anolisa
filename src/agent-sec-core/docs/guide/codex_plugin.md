---
sources:
  - design/codex_plugin
  - design/skill_ledger
  - design/code_scanner
  - design/pii_checker
---

# Codex Plugin 使用指南

## 功能概述

Codex Plugin 为 OpenAI Codex CLI 提供安全防护。通过 Codex hooks-plugin 机制注册安全 hook 脚本，在 Agent 运行时自动执行代码扫描、Prompt 检测、PII 检查和 Skill 完整性校验。

## 安装

### 一键安装

```bash
cd codex-plugin
bash install.sh
```

安装脚本自动完成：
1. 注册 marketplace 到 Codex
2. 安装插件

### 卸载

```bash
bash install.sh --remove
```

## 前置条件

1. **codex** CLI 已安装且在 PATH 中
2. **agent-sec-cli** 已安装且在 PATH 中

## 安全能力

| 能力 | Hook 点 | Matcher | 脚本 |
|------|---------|---------|------|
| 代码扫描 | PreToolUse | Bash | `code_scanner_hook.py` |
| Prompt 检测 | UserPromptSubmit | — | `prompt_scanner_hook.py` |
| PII 检测 | UserPromptSubmit + PostToolUse | — | `pii_checker_hook.py` |
| Skill 完整性 | UserPromptSubmit | — | `skill_ledger_hook.py` |

> 注：Codex 有内置沙箱机制，因此不注册 sandbox-guard hook。

## 首次启动

首次启动 Codex 后会弹出 **Hook Trust Review** 界面，需要信任以下 hook：

- `code_scanner_hook.py` — PreToolUse/Bash
- `prompt_scanner_hook.py` — UserPromptSubmit
- `pii_checker_hook.py` — UserPromptSubmit + PostToolUse
- `skill_ledger_hook.py` — UserPromptSubmit

选择 **Trust** 使 hook 生效。

## 透出模式配置

通过环境变量控制各能力的行为：

```bash
# 启动 Codex 时指定（可选）
CODE_SCANNER_MODE=deny \
PROMPT_SCANNER_MODE=deny \
SKILL_LEDGER_MODE=deny \
PII_CHECKER_MODE=deny \
codex
```

| 环境变量 | 说明 |
|---------|------|
| `CODE_SCANNER_MODE` | `observe`（默认，仅记录）/ `deny`（拦截） |
| `PROMPT_SCANNER_MODE` | `observe` / `deny` |
| `SKILL_LEDGER_MODE` | `observe` / `deny` |
| `PII_CHECKER_MODE` | `observe` / `deny` |

## 工作原理

```
Codex 启动
    └─ 读取 hooks.json
    └─ 注册 hook 脚本
         │
         ▼
    Agent 运行 → hook 触发
         │
         ├─ Python hook 脚本启动
         ├─ 调用 agent-sec-cli 子命令
         ├─ 解析结果 JSON
         └─ 输出 hook 响应
```

每个 hook 脚本是独立的 Python 进程，通过 stdin/stdout 与 Codex 交互。
