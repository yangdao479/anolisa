# Hermes Plugin 模块架构

## 概述

Hermes Plugin 是 agent-sec-core 面向 Hermes Agent 运行时的集成插件。通过 Hermes 插件框架的 `register(ctx)` 入口注册安全能力（capabilities），在 Agent 生命周期各阶段透明地执行代码扫描、Prompt 检测、PII 检查、Skill 完整性校验和可观测性采集。

## 架构组件

```
hermes-plugin/
├── src/
│   ├── __init__.py         # 插件入口 register(ctx)
│   ├── registry.py         # 配置加载 + 能力注册 + safe_hook_wrapper
│   ├── config.toml         # 能力启用配置
│   ├── plugin.yaml         # Hermes 插件元数据
│   ├── cli_runner.py       # agent-sec-cli 进程调用封装
│   ├── observability/      # 可观测性 hook 实现
│   └── capabilities/
│       ├── __init__.py     # ALL_CAPABILITIES 列表
│       ├── base.py         # AgentSecCoreCapability 基类
│       ├── code_scan.py    # 代码扫描能力
│       ├── prompt_scan.py  # Prompt 注入检测能力
│       ├── pii_scan.py     # PII/凭据检测能力
│       ├── skill_ledger.py # Skill 完整性校验能力
│       └── observability.py # 可观测性采集能力
├── scripts/                # 部署脚本
└── README.md

adapters/hermes/            # 适配器层（detect/install/uninstall 脚本）
```

## 核心数据流

```
Hermes 启动 → 加载 plugin.yaml → 调用 register(ctx)
        │
        ├─ load_config() → 读取 config.toml
        │
        ├─ register_capabilities(ctx, ALL_CAPABILITIES, config)
        │   │
        │   ├─ 遍历每个 capability
        │   ├─ 检查 config [capabilities.<id>.enabled]
        │   └─ cap.register(ctx, cap_config) → 注册 hook 回调
        │
        ▼
Agent 运行中 → hook 触发
        │
        ├─ safe_hook_wrapper 包裹（异常兜底 + 慢 hook 告警）
        │
        ├─ capability handler 执行
        │   └─ cli_runner.py → subprocess 调用 agent-sec-cli
        │       或直接 import agent_sec_cli 模块
        │
        ▼
  返回 hook 结果（pass/warn/deny + 透出信息）
```

## 关键设计

### 能力注册模型

- 每个 capability 是 `AgentSecCoreCapability` 子类，定义 `id`、`hooks`、`register()` 方法
- 配置驱动启用：`config.toml` 中 `[capabilities.<id>]` 节点控制开关
- 缺少配置节点或 `enabled` 字段时跳过该能力（fail-open）

### 安全包装器

- `safe_hook_wrapper`：所有 hook 回调外层包裹 try/except
- 异常时 log error 并返回 None（不阻断 Agent 流程）
- 超过 2s 的 hook 执行发出 slow hook 警告

### CLI 调用桥接

- `cli_runner.py` 封装 subprocess 调用 `agent-sec-cli`
- 支持传递 trace context 作为环境变量
- 解析 stdout JSON 作为 hook 返回值

### 部署与适配

- `adapters/hermes/`：适配器层提供 detect/install/uninstall 脚本
- `adapter-manifest.json` 中声明 hermes target 的 capabilities 和 actions
- 安装路径：`~/.hermes/plugins/agent-sec-core-hermes-plugin`

## 对外接口

### Hermes 插件入口

```python
def register(ctx):  # Hermes 框架在启动时调用
```

### 注册的 Hook 能力

| Capability | Hook 点 | 功能 |
|-----------|---------|------|
| code_scan | PreToolUse (Bash) | 代码安全扫描 |
| prompt_scan | UserPromptSubmit | Prompt 注入检测 |
| pii_scan | UserPromptSubmit + PostToolUse | PII/凭据检测 |
| skill_ledger | PreToolUse (skill) | Skill 完整性校验 |
| observability | 全生命周期 | 可观测性数据采集 |
