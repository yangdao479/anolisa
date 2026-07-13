# Code Scanner 模块架构

## 概述

Code Scanner 是 agent-sec-core 的静态代码安全扫描引擎，对 AI Agent 即将执行的代码片段进行安全检测。支持两种扫描模式：基于正则的规则引擎（regex）和基于本地 LLM 的语义判定引擎（llm）。

## 架构组件

```
code_scanner/
├── scanner.py          # 唯一公共入口 scan()
├── models.py           # 数据模型（Language, Severity, Verdict, Finding, ScanResult）
├── errors.py           # 分层错误码体系（100-149）
├── engine/
│   ├── regex_engine.py   # 正则规则匹配引擎
│   ├── llm_engine.py     # LLM 语义判定引擎（Ollama）
│   └── code_extractor.py # Bash 内嵌代码提取器
└── rules/
    ├── rule_loader.py    # YAML 规则加载 + 共享定义解析
    ├── bash/             # Bash 规则集（27 条 YAML）
    └── python/           # Python 规则集（10 条 YAML）
```

## 核心数据流

```
输入 (code, language, mode)
        │
        ▼
    scan() ─── mode="llm" ──► scan_with_llm() ──► Ollama API ──► ScanResult
        │
        │ mode="regex"
        ▼
  extract_inline_code()   ← Bash 中嵌套的 python/shell 代码提取
        │
        ▼
  load_rules(language)    ← YAML 规则文件加载 + _shared.yaml 引用解析
        │
        ▼
  run_regex_rules()       ← 逐规则正则匹配，支持 target_regexes 段级匹配
        │
        ▼
  _compute_verdict()      ← PASS / WARN / DENY
        │
        ▼
    ScanResult
```

## 关键设计

### 双引擎模式

| 模式 | 引擎 | 特点 |
|------|------|------|
| regex（默认） | `regex_engine.py` | 确定性、低延迟、离线可用 |
| llm | `llm_engine.py` | 语义理解、覆盖 regex 无法表达的规则 |

### 规则系统

- 规则定义：每条规则为独立 YAML 文件，包含 `rule_id`、`cwe_id`、`regex`、`severity`、`desc_en/zh`
- 共享定义：`_shared.yaml` 提供可复用的 `target_regexes` 列表，通过 `target_regexes_ref` 引用
- 段级匹配：对含 `target_regexes` 的规则，先按 `;`/`\n`/`|`/`&&` 分段，再在同一段内同时匹配主正则和目标正则
- Python 特殊处理：括号内换行折叠为空格，保证多行函数调用作为单段匹配

### LLM 引擎

- 模型服务：通过 `model_service.create_client()` 连接本地 Ollama
- 模型名：环境变量 `AGENT_SEC_OLLAMA_MODEL`，默认 `warden`
- 系统提示词：定义三大威胁类别（数据外泄/破坏性操作/恶意代码执行），输出 JSON `{verdict, reason}`
- 结果解析：优先 JSON 解析，降级为文本中 PASS/DENY 关键字识别

### 错误码分层

| 范围 | 层级 | 示例 |
|------|------|------|
| 100 | 基础 | CodeScanError |
| 110-119 | 输入层 | ErrInputEmpty, ErrUnsupportedLang |
| 120-129 | 规则层 | ErrRuleYamlParse, ErrRegexCompile |
| 130-139 | 引擎层 | ErrEngineTimeout, ErrEngineResource |
| 140-149 | LLM 层 | ErrLlmUnavailable, ErrLlmUnparsable |

## 对外接口

### 公共 API

```python
def scan(code: str, language: Language, *, rules: list[str] | None = None, mode: str = "regex") -> ScanResult
```

### 输出模型

- `ScanResult`: ok, verdict, summary, findings, language, engine_version, elapsed_ms
- `Finding`: rule_id, severity, desc_zh, desc_en, evidence
- `Verdict`: PASS / WARN / DENY / ERROR

### 被调用方

- `security_middleware/backends/code_scan.py` — 通过 middleware invoke 调用
- Hook 脚本（cosh/codex/hermes/openclaw）— 直接 import 或通过 CLI 调用
