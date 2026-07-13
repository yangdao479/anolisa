# Codex Plugin 模块架构

## 概述

Codex Plugin 是 agent-sec-core 面向 OpenAI Codex CLI 的集成插件。通过 Codex 的 hooks-plugin 机制注册安全 hook 脚本，在 Agent 生命周期中执行代码扫描、Prompt 检测、PII 检查和 Skill 完整性校验。采用 Python hook 脚本 + marketplace 注册的部署模型。

## 架构组件

```
codex-plugin/
├── install.sh              # 一键安装/卸载脚本
├── .agents/
│   └── plugins/
│       └── marketplace.json  # Codex marketplace 注册清单
├── hooks-plugin/
│   ├── .codex-plugin/
│   │   └── ...             # Codex 插件元数据
│   └── hooks/
│       ├── hooks.json              # hook 注册声明
│       ├── code_scanner_hook.py    # PreToolUse/Bash 代码扫描
│       ├── prompt_scanner_hook.py  # UserPromptSubmit Prompt 检测
│       ├── pii_checker_hook.py     # UserPromptSubmit + PostToolUse PII 检测
│       ├── skill_ledger_hook.py    # UserPromptSubmit Skill 完整性
│       └── trace_context.py        # trace context 传播工具
└── README.md
```

## 核心数据流

```
install.sh 执行
      │
      ├─ codex plugin marketplace add <dir>  → 注册 marketplace
      └─ codex plugin add agent-sec-core@agent-sec → 安装插件
      │
      ▼
Codex 启动 → 读取 hooks.json → 注册 hook
      │
      ▼
Agent 运行中 → hook 触发
      │
      ├─ PreToolUse/Bash:
      │   ├─ code_scanner_hook.py → agent-sec-cli code-scan
      │   └─ (无沙箱 hook，Codex 有内置沙箱)
      │
      ├─ UserPromptSubmit:
      │   ├─ prompt_scanner_hook.py → agent-sec-cli prompt-scan
      │   ├─ pii_checker_hook.py → agent-sec-cli pii-scan
      │   └─ skill_ledger_hook.py → agent-sec-cli skill-ledger
      │
      └─ PostToolUse:
          └─ pii_checker_hook.py → agent-sec-cli pii-scan
      │
      ▼
  Hook 输出 JSON → Codex 处理（pass/warn/deny）
```

## 关键设计

### 部署模型

- **Marketplace 注册**：`install.sh` 将 `codex-plugin/` 目录注册为本地 marketplace
- **插件安装**：通过 `codex plugin add` 将 hooks-plugin 安装到 Codex
- **信任审批**：首次启动时弹出 Hook Trust Review，用户需信任各 hook 脚本

### Hook 注册声明

`hooks.json` 声明各 hook 的触发点和脚本路径：
- `PreToolUse` + matcher `Bash` → `code_scanner_hook.py`
- `UserPromptSubmit` → `prompt_scanner_hook.py`, `pii_checker_hook.py`, `skill_ledger_hook.py`
- `PostToolUse` → `pii_checker_hook.py`

### 环境变量控制透出模式

| 环境变量 | 作用 | 可选值 |
|---------|------|--------|
| `CODE_SCANNER_MODE` | 代码扫描透出模式 | `observe`（默认）/ `deny` |
| `PROMPT_SCANNER_MODE` | Prompt 检测透出模式 | `observe` / `deny` |
| `SKILL_LEDGER_MODE` | Skill 完整性透出模式 | `observe` / `deny` |
| `PII_CHECKER_MODE` | PII 检测透出模式 | `observe` / `deny` |

- `observe`：仅记录日志，不拦截
- `deny`：检测到风险时强制拦截

### Trace Context 传播

- `trace_context.py` 从 Codex 传递的环境变量或 stdin 中提取 session_id、run_id 等
- 确保安全事件与 Agent 会话正确关联

## 对外接口

### 安装 CLI

```bash
bash install.sh          # 安装
bash install.sh --remove # 卸载
```

### 前置条件

1. `codex` CLI 已安装且在 PATH 中
2. `agent-sec-cli` 已安装且在 PATH 中
