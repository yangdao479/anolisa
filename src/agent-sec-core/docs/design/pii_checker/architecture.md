# PII Checker 模块架构

## 概述

PII Checker 是 agent-sec-core 的个人身份信息（PII）及凭据检测模块。对文本内容进行扫描，识别敏感信息（如身份证号、API Key、JWT 等），支持置信度评分、自动脱敏、多检测器编排。

## 架构组件

```
pii_checker/
├── __init__.py       # 公共 API 导出
├── scanner.py        # 检测编排器 PiiScanner + 便捷函数 scan_text()
├── models.py         # 数据模型（PiiFinding, PiiScanResult, PiiSeverity, Verdict）
├── redactor.py       # 脱敏工具（按 type 策略遮蔽）
├── audit.py          # 审计日志（PII 检测事件记录）
├── validators.py     # 校验器（Luhn、日期范围等降低误报）
├── cli.py            # CLI 子命令（pii-scan）
└── detectors/
    ├── base.py       # PiiDetector 协议 + PiiCandidate 模型
    └── regex.py      # RegexPiiDetector 默认实现
```

## 核心数据流

```
输入 (text, source, options)
        │
        ▼
  PiiScanner.scan()
        │
        ├─ _limit_text()       ← 按 max_bytes 截断 UTF-8 安全前缀
        │
        ├─ _detect()           ← 遍历所有 detector，收集 PiiCandidate
        │   └─ RegexPiiDetector.detect()  ← 正则模式匹配 + validators
        │
        ├─ _dedupe()           ← span 重叠去重（保留高置信度/高严重度）
        │
        ├─ _build_findings()   ← 过滤低置信度、构造 PiiFinding 列表
        │
        ├─ _aggregate_verdict() ← DENY > WARN > PASS
        │
        └─ redact_text()       ← 可选：输出脱敏后文本
        │
        ▼
  PiiScanResult
```

## 关键设计

### 检测器架构

- **协议驱动**：`PiiDetector` 为抽象协议，定义 `detect(text) -> list[PiiCandidate]`
- **可插拔**：默认使用 `RegexPiiDetector`，支持注入自定义检测器（如 NER 模型）
- **多类型**：PiiCategory 分为 `personal_data`（身份证、手机号等）和 `credential`（API Key、JWT 等）

### 去重策略

- 按 (severity DESC, confidence DESC, span_start ASC, span_length DESC) 排序
- 后续 candidate 如与已保留项 span 重叠则丢弃
- 特殊处理：`bearer_token` 与 `jwt` 同 span 允许共存（多类型标注）

### 置信度与严重度

- `PiiSeverity`: WARN（告警级）/ DENY（必须拦截）
- 低置信度阈值：0.5，默认不输出低置信 findings（可通过 `include_low_confidence=True` 开启）
- Validators（Luhn 等）用于提升正则匹配的置信度

### 数据源标注

- `source` 参数标记文本来源：`user_input`, `tool_input`, `tool_output`, `model_output`, `observability`, `manual`
- 用于后续审计和策略决策

## 对外接口

### 公共 API

```python
def scan_text(text: str, *, source: str = "unknown", ...) -> PiiScanResult
class PiiScanner:
    def scan(self, text: str, *, source: str = "unknown", ...) -> PiiScanResult
```

### 输出模型

- `PiiScanResult`: ok, verdict, summary(dict), findings, elapsed_ms, redacted_text
- `PiiFinding`: type, category, severity, confidence, evidence_redacted, span, metadata

### 被调用方

- `security_middleware/backends/pii_scan.py` — 通过 middleware invoke 调用
- Hook 脚本（cosh/codex/hermes/openclaw）— 在多个生命周期点调用
- CLI 子命令 `agent-sec-cli pii-scan`
