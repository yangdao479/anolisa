---
sources:
  - design/openclaw_plugin
  - design/skill_ledger
  - design/code_scanner
  - design/pii_checker
---

# OpenClaw Plugin 使用指南

## 功能概述

OpenClaw Plugin 为 OpenClaw Agent 运行时提供安全防护。通过 OpenClaw Plugin SDK 注册安全能力，在 Agent hook 生命周期中自动执行安全检测。支持通过 Dashboard UI 配置各能力的启用状态和策略。

## 安装

### 通过适配器安装

```bash
adapters/openclaw/scripts/install.sh
```

### 手动安装

```bash
cd openclaw-plugin
npm install
npm run build
# 将 dist/ 注册到 OpenClaw 插件目录
```

## 安全能力

| 能力 | ID | Hook 点 | 功能 |
|------|-----|---------|------|
| 代码扫描 | `scan-code` | before_tool_call (Bash) | 检测危险代码 |
| Prompt 检测 | `prompt-scan` | before_dispatch | 检测 Prompt 注入 |
| PII 检测 | `pii-scan-user-input` | before_dispatch + post_tool_call | 检测敏感信息 |
| Skill 完整性 | `skill-ledger` | before_tool_call (skill) | 校验 Skill 合法性 |
| 可观测性 | `observability` | 全生命周期 | 采集运行指标 |

## 配置

在 OpenClaw 设置中配置 `agent-sec` 插件：

```json
{
  "promptScanBlock": false,
  "piiScanUserInput": true,
  "piiIncludeLowConfidence": false,
  "codeScanRequireApproval": false,
  "capabilities": {
    "scan-code": { "enabled": true },
    "prompt-scan": { "enabled": true },
    "pii-scan-user-input": {
      "enabled": true,
      "enableBlock": false
    },
    "skill-ledger": {
      "enabled": true,
      "policy": "ask"
    },
    "observability": { "enabled": true }
  }
}
```

### 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `promptScanBlock` | DENY 时直接拦截请求 | `false` |
| `piiScanUserInput` | 扫描用户输入中的 PII | `true` |
| `piiIncludeLowConfidence` | 包含低置信度 findings | `false` |
| `codeScanRequireApproval` | 代码问题需用户审批 | `false` |
| `capabilities.*.enabled` | 各能力独立开关 | `true` |
| `capabilities.skill-ledger.policy` | ask/debug/warn/block | `"ask"` |

## Dashboard UI

OpenClaw 管理界面中可直接配置：
- **Prompt 扫描拦截模式**：是否对 DENY 结果直接拦截
- **PII 用户输入扫描**：开关
- **代码扫描审批模式**：检测到问题时弹出审批卡片
- **能力配置**：各能力的启用/禁用

## 前置条件

1. OpenClaw Agent 运行时已部署
2. `agent-sec-cli` 已安装且在 PATH 中
3. Node.js 运行时可用（插件为 TypeScript 编译）

## 卸载

```bash
adapters/openclaw/scripts/uninstall.sh
```
