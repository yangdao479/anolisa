---
sources:
  - design/pii_checker
---

# PII Checker 使用指南

## 功能概述

PII Checker 检测文本中的个人身份信息（PII）和凭据（如 API Key、JWT、私钥等）。支持多生命周期点扫描（用户输入、工具输入/输出、模型响应），提供自动脱敏和置信度评分。

## 使用方式

### CLI 命令

```bash
# 扫描文本
agent-sec-cli scan-pii --text '我的身份证号是 xxxxx'

# 从 stdin 读取
echo "Bearer xxxxx..." | agent-sec-cli scan-pii --stdin

# 扫描文件
agent-sec-cli scan-pii --input ./config.env

# 包含脱敏输出
agent-sec-cli scan-pii --text '...' --redact-output

# 包含低置信度结果
agent-sec-cli scan-pii --text '...' --include-low-confidence
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--text` | 待扫描文本 | — |
| `--stdin` | 从 stdin 读取 | false |
| `--input` | 文本文件路径 | — |
| `--format` | 输出格式：`json` / `text` | `json` |
| `--include-low-confidence` | 包含低置信度 findings | false |
| `--raw-evidence` | 输出原始证据（调试用） | false |
| `--redact-output` | 输出脱敏后文本 | false |
| `--source` | 数据来源标注 | `unknown` |
| `--max-bytes` | 最大扫描字节数 | 1MB |

### 输出格式

```json
{
  "ok": true,
  "verdict": "warn",
  "summary": {
    "total": 1,
    "by_type": {"id_card_cn": 1},
    "by_category": {"personal_data": 1},
    "by_severity": {"warn": 1},
    "source": "user_input",
    "bytes_scanned": 42,
    "truncated": false
  },
  "findings": [
    {
      "type": "id_card_cn",
      "category": "personal_data",
      "severity": "warn",
      "confidence": 0.95,
      "evidence_redacted": "110101****8888",
      "span": {"start": 8, "end": 26}
    }
  ],
  "elapsed_ms": 5
}
```

## 检测类型

### 个人数据（personal_data）

- 中国身份证号
- 手机号码
- 邮箱地址
- 银行卡号

### 凭据（credential）

- API Key / Secret Key
- JWT / Bearer Token
- 私钥文件内容
- 数据库连接串
- AWS Access Key

## 多生命周期点覆盖

PII Checker 在 Agent 运行的多个阶段执行扫描：

| 阶段 | 扫描对象 | Hook 点 |
|------|---------|---------|
| 用户输入 | 用户提交的消息 | UserPromptSubmit |
| 工具输入 | 传递给工具的参数 | PreToolUse |
| 模型输出 | LLM 返回的文本 | AfterModel |
| 工具输出 | 工具执行的结果 | PostToolUse |
| 工具失败 | 工具错误输出 | PostToolUseFailure |

## 配置选项

### 环境变量

```bash
export PII_CHECKER_MODE=deny  # deny: 拦截; observe: 仅记录（默认）
```

### OpenClaw 插件配置

```json
{
  "piiScanUserInput": true,
  "piiIncludeLowConfidence": false,
  "capabilities": {
    "pii-scan-user-input": {
      "enabled": true,
      "enableBlock": false
    }
  }
}
```

### Hermes 插件配置（config.toml）

```toml
[capabilities.pii-scan-user-input]
enabled = true
timeout = 10
include_low_confidence = false
warning_ttl_seconds = 300
```

## Severity 与 Verdict

- **WARN**：检测到 PII 但非高危（如邮箱）→ 告警并放行
- **DENY**：检测到高危凭据（如私钥、数据库密码）→ 建议拦截
- Verdict 取所有 findings 中的最高严重度
