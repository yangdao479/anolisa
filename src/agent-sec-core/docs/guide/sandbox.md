---
sources:
  - design/sandbox
---

# Sandbox 使用指南

## 功能概述

Sandbox 为 AI Agent 执行的 shell 命令提供安全隔离环境。通过四层命令分类自动决定安全策略，再由 linux-sandbox 二进制在 Linux 内核级别实施文件系统和网络隔离，确保命令在受限环境中执行。

## 工作原理

```
用户/Agent 提交命令
      │
      ▼
  命令分类（四层）
      │
      ├─ destructive → 直接拒绝，不执行
      ├─ dangerous   → 沙箱执行（workspace-write，禁止补权限）
      ├─ safe        → 沙箱执行（read-only）
      └─ default     → 沙箱执行（workspace-write，可自动补权限）
      │
      ▼
  linux-sandbox 隔离执行
```

## 命令分类

### Destructive（毁灭性）

直接拒绝执行，不进入沙箱：
- `rm -rf /`、`mkfs.*`
- `dd if=/dev/zero of=/dev/sda`
- 系统关键路径的不可逆删除

### Dangerous（危险）

沙箱执行但禁止自动补权限：
- 所有 `sudo` 命令
- `curl | bash` 等远程代码执行
- 修改系统配置的命令

### Safe（安全）

沙箱只读模式执行：
- `ls`、`cat`、`grep`、`find` 等只读命令
- `git status`、`git log`、`git diff`
- `sed`（无 `-i` 参数时）

### Default（默认）

沙箱执行 + 可自动补最小权限：
- 未命中以上分类的命令
- 自动检测是否需要网络或额外写路径

## 使用方式

### CLI 命令（策略预览）

```bash
# 查看命令分类
python3 -m agent_sec_cli.sandbox.classify_command "git status"
python3 -m agent_sec_cli.sandbox.classify_command --json "rm -rf /"

# 生成沙箱策略
python3 -m agent_sec_cli.sandbox.sandbox_policy --cwd /workspace "npm install"
```

### Hook 自动触发

在 Agent 运行时中，sandbox-guard hook 在执行 shell 命令前自动触发：

| 运行时 | Hook | 说明 |
|--------|------|------|
| Cosh | PreToolUse (shell) | sandbox-guard.py 执行分类+沙箱 |
| Hermes | PreToolUse (Bash) | 通过 capability 注册 |
| Codex | — | Codex 有内置沙箱机制 |

## 沙箱隔离能力

### 文件系统隔离

- 基于 Bubblewrap 构建受限文件系统视图
- 只读挂载系统目录（/usr、/lib 等）
- 可写路径限制为工作目录和 /tmp
- 可通过权限规则添加额外写路径

### 网络隔离

- 默认禁止网络访问（seccomp 阻断网络系统调用）
- 需要网络时可通过权限规则启用
- 支持 proxy 模式（network namespace + 路由桥接）

### 进程隔离

- `no_new_privs` 防止权限提升
- Seccomp BPF 过滤危险系统调用

## 沙箱失败处理

当沙箱执行失败时（PostToolUseFailure），`sandbox-failure-handler.py` 提供用户友好的错误信息，帮助理解失败原因（如权限不足、网络被禁止等）。

## 平台要求

- **linux-sandbox 二进制**：仅支持 Linux（需要 bubblewrap、seccomp 等内核支持）
- **命令分类**：跨平台可用（Python 实现）
- macOS/Windows 上分类正常工作，但无实际沙箱隔离
