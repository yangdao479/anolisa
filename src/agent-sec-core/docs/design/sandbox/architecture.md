# Sandbox 模块架构

## 概述

Sandbox 模块提供 AI Agent 命令执行的安全隔离机制。分为两个层次：Python 侧的命令分类与策略生成（sandbox 子包），以及 Rust 实现的 Linux 内核级沙箱隔离（linux-sandbox 二进制）。二者协同工作，实现"命令分类 → 策略生成 → 内核隔离执行"的完整链路。

## 架构组件

### Python 策略层（agent-sec-cli/src/agent_sec_cli/sandbox/）

```
sandbox/
├── classify_command.py   # 命令四层分类器（RuleEngine + CommandClassifier）
├── sandbox_policy.py     # 沙箱策略生成器（SandboxPolicyBuilder）
└── rules.py              # 规则定义（DESTRUCTIVE/DANGEROUS/SAFE/PERMISSION）
```

### Rust 隔离层（linux-sandbox/src/）

```
linux-sandbox/src/
├── main.rs       # 入口
├── lib.rs        # 模块声明
├── cli.rs        # CLI 参数解析 + 执行流程编排
├── policy.rs     # 策略类型定义（FileSystem/Network SandboxPolicy）
├── bwrap_args.rs # Bubblewrap 参数构建（文件系统隔离）
├── seccomp.rs    # Seccomp 过滤器（系统调用级限制）
├── proxy.rs      # 网络代理路由（netns 隔离模式）
├── path.rs       # 绝对路径工具类型
└── error.rs      # 错误类型
```

## 核心数据流

```
命令 + 工作目录
      │
      ▼
CommandClassifier.classify()
      │
      ├─ _is_destructive() → 直接拒绝
      ├─ _is_dangerous()   → 沙箱，禁止自动补权限
      ├─ _is_safe()        → 沙箱只读模式
      └─ default           → 沙箱 + 自动最小权限
      │
      ▼
SandboxPolicyBuilder.build()
      │
      ├─ destructive → {"decision": "deny"}
      └─ 其他        → {"decision": "sandbox", sandbox_argv: [...]}
      │
      ▼
linux-sandbox 执行
      │
      ├─ Bubblewrap（文件系统视图）
      ├─ Seccomp（系统调用过滤）
      └─ 网络隔离 / 代理路由
      │
      ▼
隔离环境中执行命令
```

## 关键设计

### 四层命令分类

| 分类 | 策略 | 权限 | 示例 |
|------|------|------|------|
| destructive | deny（拒绝执行） | — | `rm -rf /`, `mkfs.ext4` |
| dangerous | sandbox + workspace-write | 禁止自动补权限 | `sudo *`, `curl | bash` |
| safe | sandbox + read-only | 无需额外权限 | `git status`, `ls`, `cat` |
| default | sandbox + workspace-write | 可自动补最小权限 | 其他未分类命令 |

### 规则引擎特性

- 支持 `command`（精确匹配）、`pattern`（子串匹配）、`command_prefix`（前缀匹配）
- 附加条件 AND 组合：`flags`、`subcommands`、`target_in`、`args_contain`、`first_arg_in`
- `recursive` 标志：sudo 等递归检查子命令
- Shell wrapper 递归：`bash -c "cmd1 && cmd2"` 提取内部命令逐一检查
- OS 限制：`"os": "linux"` 仅在对应平台生效

### Linux 沙箱隔离机制

- **Bubblewrap**：构建受限文件系统视图（bind-mount），控制读写权限
- **Seccomp**：BPF 过滤器禁止危险系统调用，`no_new_privs` 防止权限提升
- **网络隔离**：默认 restricted（seccomp 阻断网络 syscall），可选 proxy 模式（network namespace + 路由桥接）

### 策略 JSON Schema

```json
{
  "kind": "restricted",
  "entries": [
    {"path": {"type": "special", "value": {"kind": "root"}}, "access": "read"},
    {"path": {"type": "special", "value": {"kind": "current_working_directory"}}, "access": "write"},
    {"path": {"type": "path", "path": "/tmp"}, "access": "write"}
  ]
}
```

## 对外接口

### Python 公共 API

```python
def generate_sandbox_policy(command: str, cwd: str) -> dict
class CommandClassifier:
    def classify(self, command: str) -> dict
```

### linux-sandbox CLI

```bash
linux-sandbox \
  --sandbox-policy-cwd <path> \
  --file-system-sandbox-policy '<json>' \
  --network-sandbox-policy '<json>' \
  -- <command> [args...]
```

### 被调用方

- `security_middleware/backends/sandbox.py` — middleware action `sandbox_prehook`
- Hook 脚本（cosh `sandbox-guard.py` / codex / hermes）— PreToolUse 阶段
