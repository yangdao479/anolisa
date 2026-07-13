# Cosh Extension 模块架构

## 概述

Cosh Extension 是 agent-sec-core 面向 Cosh Agent 运行时的扩展。通过 `cosh-extension.json` 声明式配置在多个 hook 生命周期点注册安全 Python 脚本，提供代码扫描、Prompt 注入检测、PII 检查、沙箱执行、Skill 完整性校验和可观测性采集的完整安全防护。

## 架构组件

```
cosh-extension/
├── cosh-extension.json     # 扩展声明（hook 注册 + 版本 + matcher）
├── commands/               # 自定义命令（可选）
└── hooks/
    ├── code_scanner_hook.py      # PreToolUse/shell 代码扫描
    ├── prompt_scanner_hook.py    # UserPromptSubmit Prompt 注入检测
    ├── pii_checker_hook.py       # 多点 PII/凭据检测
    ├── sandbox-guard.py          # PreToolUse/shell 沙箱策略执行
    ├── sandbox-failure-handler.py # PostToolUseFailure 沙箱失败处理
    ├── skill_ledger_hook.py      # PreToolUse/skill Skill 完整性校验
    ├── observability_hook.py     # 全生命周期可观测性采集
    └── trace_context.py          # trace context 传播工具
```

## 核心数据流

```
Cosh 启动 → 读取 cosh-extension.json → 注册 hook
        │
        ▼
Agent 运行中 → hook 触发
        │
        ├── PreToolUse (matcher: "skill"):
        │   └─ skill_ledger_hook.py → security_middleware.invoke("skill_ledger")
        │
        ├── PreToolUse (matcher: "^(run_shell_command|shell)$"):
        │   ├─ code_scanner_hook.py → security_middleware.invoke("code_scan")
        │   └─ sandbox-guard.py → generate_sandbox_policy() → linux-sandbox
        │
        ├── PreToolUse (all):
        │   ├─ pii_checker_hook.py → security_middleware.invoke("pii_scan")
        │   └─ observability_hook.py → record_observability()
        │
        ├── UserPromptSubmit:
        │   ├─ prompt_scanner_hook.py → security_middleware.invoke("prompt_scan")
        │   ├─ pii_checker_hook.py → PII 扫描用户输入
        │   └─ observability_hook.py
        │
        ├── BeforeModel / AfterModel:
        │   ├─ pii_checker_hook.py (AfterModel) → PII 扫描模型输出
        │   └─ observability_hook.py
        │
        ├── PostToolUse:
        │   ├─ pii_checker_hook.py → PII 扫描工具输出
        │   └─ observability_hook.py
        │
        └── PostToolUseFailure:
            ├─ pii_checker_hook.py → PII 扫描错误输出
            ├─ sandbox-failure-handler.py → 处理沙箱执行失败
            └─ observability_hook.py
```

## 关键设计

### 声明式 Hook 注册

`cosh-extension.json` 使用 JSON 配置声明 hook：
- `hooks.<lifecycle_point>[].matcher`: 正则匹配工具名（可选，不设则匹配所有）
- `hooks.<lifecycle_point>[].hooks[].type`: 固定为 `"command"`
- `hooks.<lifecycle_point>[].hooks[].command`: Python 脚本路径（`${extensionPath}` 变量）
- `hooks.<lifecycle_point>[].hooks[].timeout`: 超时时间（ms）

### 生命周期覆盖

| Hook 点 | 注册的脚本 |
|---------|-----------|
| PreToolUse | skill_ledger, code_scanner, sandbox-guard, pii_checker, observability |
| UserPromptSubmit | prompt_scanner, pii_checker, observability |
| BeforeModel | observability |
| AfterModel | pii_checker, observability |
| PostToolUse | pii_checker, observability |
| PostToolUseFailure | pii_checker, sandbox-failure-handler, observability |
| Stop | observability |

### 沙箱集成

- `sandbox-guard.py`：最复杂的 hook，集成命令分类 + 策略生成 + linux-sandbox 调用
- `sandbox-failure-handler.py`：沙箱执行失败时的用户友好错误处理
- 通过 `${extensionPath}` 解析沙箱二进制路径

### Trace Context

- `trace_context.py` 从 Cosh 传递的 stdin JSON 中提取 session_id、run_id、tool_call_id
- 设置 `correlation_context` 确保安全事件与会话正确关联

## 对外接口

### 扩展入口

由 Cosh 运行时自动发现并加载 `cosh-extension.json`。

### 安装路径

- 系统安装：`/usr/share/anolisa/extensions/agent-sec-core`
- 开发模式：直接指向源码目录
