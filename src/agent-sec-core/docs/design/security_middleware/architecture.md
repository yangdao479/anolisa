# Security Middleware 模块架构

## 概述

Security Middleware 是 agent-sec-core 所有安全能力的统一调度层。提供单一入口 `invoke(action, **kwargs)` 将请求路由到对应的后端实现，同时透明地完成请求上下文构造、生命周期事件记录、错误处理。

## 架构组件

```
security_middleware/
├── __init__.py    # 公共入口 invoke() + caller 自动检测
├── router.py      # Action → Backend 静态路由注册表
├── context.py     # RequestContext 数据类（trace_id, session_id, caller 等）
├── lifecycle.py   # 生命周期钩子（pre/post/error → SecurityEvent 记录）
├── result.py      # ActionResult 统一返回类型
└── backends/
    ├── base.py           # BaseBackend 抽象基类
    ├── code_scan.py      # 代码扫描后端
    ├── prompt_scan.py    # 提示词扫描后端
    ├── pii_scan.py       # PII 检测后端
    ├── sandbox.py        # 沙箱策略后端
    ├── skill_ledger.py   # Skill 完整性校验后端
    ├── hardening.py      # 加固后端
    ├── asset_verify.py   # 资产校验后端
    └── summary.py        # 安全摘要后端
```

## 核心数据流

```
调用方 (hook / CLI / daemon)
        │
        ▼
  invoke(action, caller=None, **kwargs)
        │
        ├─ 构造 RequestContext（auto trace_id, timestamp, caller detection）
        │
        ├─ router.get_backend(action) → 实例化/缓存 Backend
        │
        ├─ lifecycle.pre_action()   ← 当前 no-op（单事件模型）
        │
        ├─ backend.execute(ctx, **kwargs) → ActionResult
        │
        ├─ lifecycle.post_action()  ← SecurityEvent + Telemetry 记录
        │   或 lifecycle.on_error() ← 异常时记录失败事件
        │
        ▼
  ActionResult (success, exit_code, data, stdout, stderr)
```

## 关键设计

### 单入口模式

- 所有安全能力通过 `invoke(action)` 统一调用，不暴露具体 Backend 实现
- Caller 自动检测：通过栈帧回溯识别入口脚本（sandbox-guard.py → "sandbox-guard", cli.py → "cli"）

### 路由注册表

| Action | Backend | 功能 |
|--------|---------|------|
| `sandbox_prehook` | SandboxBackend | 命令分类 + 沙箱策略 |
| `code_scan` | CodeScanBackend | 代码安全扫描 |
| `prompt_scan` | PromptScanBackend | 提示词注入检测 |
| `pii_scan` | PiiScanBackend | PII/凭据检测 |
| `skill_ledger` | SkillLedgerBackend | Skill 完整性校验 |
| `harden` | HardeningBackend | 安全加固 |
| `verify` | AssetVerifyBackend | 资产校验 |
| `summary` | SummaryBackend | 安全事件摘要 |

- 静态映射，不支持运行时热替换
- 惰性实例化 + 缓存（首次访问时创建 Backend 单例）

### 单事件生命周期模型

- 每次 `invoke()` 只产生一条 SecurityEvent（非 request + response 两条）
- `post_action`：成功时 merge request kwargs + result data 为一条完成事件
- `on_error`：失败时 merge request kwargs + error details 为一条失败事件
- 事件同时写入 SecurityEvent 存储和 Telemetry

### RequestContext 关联

- `trace_id`：从全局 TraceContext 继承或自动生成 UUID
- `session_id`/`run_id`/`call_id`/`tool_call_id`：从 correlation_context 传播
- `invocation_id`：进程级 CLI 调用标识

## 对外接口

### 公共 API

```python
def invoke(action: str, *, caller: str | None = None, **kwargs) -> ActionResult
```

### 被调用方

- 各 hook 脚本通过 `from agent_sec_cli.security_middleware import invoke` 直接调用
- Daemon handler 内部调用
- CLI 子命令内部调用
