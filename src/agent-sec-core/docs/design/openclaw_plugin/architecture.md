# OpenClaw Plugin 模块架构

## 概述

OpenClaw Plugin 是 agent-sec-core 面向 OpenClaw Agent 运行时的 TypeScript 插件。遵循 OpenClaw Plugin SDK 规范，通过 `definePluginEntry` 注册安全能力，在 Agent 的 hook 生命周期中执行安全检测。底层通过 subprocess 调用 `agent-sec-cli` 完成实际扫描。

## 架构组件

```
openclaw-plugin/
├── src/
│   ├── index.ts            # 插件入口（definePluginEntry + register）
│   ├── registration.ts     # 能力启用检查（isCapabilityEnabled）
│   ├── types.ts            # SecurityCapability 类型定义
│   ├── utils.ts            # CLI 调用封装 + JSON 解析 + 错误处理
│   ├── helpers/            # 通用辅助
│   └── capabilities/
│       ├── code-scan.ts    # 代码扫描能力
│       ├── prompt-scan.ts  # Prompt 注入检测能力
│       ├── pii-scan.ts     # PII/凭据检测能力
│       ├── skill-ledger.ts # Skill 完整性校验能力
│       └── observability.ts # 可观测性采集能力
├── openclaw.plugin.json    # 插件元数据 + configSchema
├── package.json            # npm 依赖
├── tsconfig.json           # TypeScript 配置
├── tests/                  # 测试
├── scripts/                # 部署脚本
└── README.md

adapters/openclaw/          # 适配器层（detect/install/uninstall 脚本）
```

## 核心数据流

```
OpenClaw 启动 → 加载 openclaw.plugin.json → 调用 register(api)
        │
        ├─ 读取 pluginConfig.capabilities 配置
        │
        ├─ 遍历 capabilities 数组
        │   ├─ isCapabilityEnabled(cap, cfg) → 检查开关
        │   └─ cap.register(api) → 注册 hook 回调
        │
        ▼
Agent 运行中 → hook 触发 (before_dispatch / before_tool_call / ...)
        │
        ├─ capability handler 执行
        │   └─ utils.ts → child_process.execFile("agent-sec-cli", [...])
        │       → 解析 stdout JSON
        │
        ▼
  返回 hook 结果（继续/警告/拦截）
```

## 关键设计

### 插件配置模型

`openclaw.plugin.json` 的 `configSchema` 定义了用户可配置项：
- `promptScanBlock`: 检测到 DENY 时是否直接拦截
- `piiScanUserInput`: 是否扫描用户输入中的 PII
- `piiIncludeLowConfidence`: 是否包含低置信度 findings
- `codeScanRequireApproval`: 代码扫描检测到问题时是否要求审批
- `capabilities.*`: 各能力的 enabled 开关和策略配置

### 能力与 Hook 映射

| Capability | Hook 点 | 功能 |
|-----------|---------|------|
| scan-code | before_tool_call (Bash) | 代码安全扫描 |
| prompt-scan | before_dispatch | Prompt 注入检测 |
| pii-scan-user-input | before_dispatch + post_tool_call | PII/凭据检测 |
| skill-ledger | before_tool_call (skill) | Skill 完整性校验（policy: ask/debug/warn/block） |
| observability | 全生命周期 | 可观测性数据采集 |

### CLI 调用封装

- `utils.ts` 通过 `child_process` 调用 `agent-sec-cli` 子命令
- 传递 trace context 作为环境变量（session_id, run_id 等）
- 处理进程超时、非零退出码、JSON 解析失败等异常情况

### 部署与适配

- `adapters/openclaw/`：适配器层提供 detect/install/uninstall 脚本
- `adapter-manifest.json` 中声明 openclaw target 的 plugins、skills、hooks
- 编译产物：`dist/index.js`（TypeScript 编译输出）

## 对外接口

### OpenClaw 插件入口

```typescript
export default definePluginEntry({
  id: "agent-sec",
  name: "Agent Security",
  register(api) { ... }
})
```

### UI Hints

`openclaw.plugin.json` 中的 `uiHints` 为各配置项提供 Dashboard 展示标签和描述，支持用户在 OpenClaw 管理界面直接配置安全策略。
