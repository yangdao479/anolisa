# Secret Gateway：外发类凭据的出站注入

## 1. 背景与目标

Agent 要调用外部服务就必须持有凭据，而 Agent 是不可信实体——模型输出不确定，且持续暴露在直接与间接提示注入之下。只要凭据落在 Agent 可读范围内，它就可能随上下文发给模型提供商、被 Agent 滥用，或在注入操纵下被直接发给攻击者。

更根本的约束是：**在现有 OS 权限模型下，「能用」即「能读」**。让 Agent 持有凭据却只准正确使用、不准读取转发，在权限模型层面不成立。因此唯一能整体消除攻击面（而不是降低泄漏概率）的做法是：**凭据本体从一开始就不进入 Agent 的权限范围，只把「使用」这一动作暴露给它。**

本文只覆盖**外发类凭据**——凭据本体随请求离开本机的那一类（GitHub / Gitee Token、云 API Key、OAuth Bearer Token 等）。本期实现范围进一步收敛为 GitHub REST 的 `Authorization` 头。

> 分类边界：SSH / GPG / 代码签名私钥属**内用类**（本地运算、私钥不出网），不在本文范围。特别注意 AWS SigV4 一类**客户端签名式**凭据虽然走标准 HTTPS，但出网的只是签名、密钥本体从不离开机器，按判据归内用类，网关不应也无法对其做 header 替换。

## 2. 设计目标与非目标

**硬目标一：凭据本体全程不进入 Agent 可达范围。** Agent 手里只有一把无效的 fake token。

**硬目标二：对 Agent 零侵入。** 不改 Agent 实现、不改工具定义，Agent 照常「以为」自己具备条件并正常行动。

**非目标（本期不做，列出以免误解）**：

- **入站回替**：扫描响应把回流的真 token（如 OAuth `access_token`）换回 fake。
- **DLP**：请求/响应体的敏感信息检测与阻断脱敏。
- **越权拦截**：目前 host 不命中即放行（passthrough），尚未实现「携带受管凭据发往白名单外 host 则拒绝并审计」。
- **git-over-HTTPS**：`git clone/push` 走 `Authorization: Basic`，本期只处理 REST 的 `Bearer` / `token` 形态。
- **授权范围内的滥用（混淆代理）**：被注入劫持的 Agent 在白名单内发起语义有害的合法调用，无法靠凭据隔离消除，只能靠白名单粒度、人工确认与事后审计收窄。

## 3. 信任域

按 uid 分两域，隔离由 OS 强制而非约定：

| 域 | 成员 | 持有什么 |
| --- | --- | --- |
| root（可信） | `agent-sec-daemon`、它托管的 mitmdump 子进程 | 配置文件（内含真凭据，`0600` root）、CA |
| 普通用户（不可信） | Agent 及其工具链 | 仅 fake token；信任 proxy 的自签 CA |

普通用户无法 ptrace root 进程、读不到 root 进程的 `/proc/<pid>/environ`，也读不到 `0600` root-owned 的配置文件——这就是「Agent 读不到真 token」的硬保证。

## 4. 数据面与策略面分离

数据面（TLS 终结、连接处理）用 mitmproxy，策略面留在 daemon。

**mitmproxy 以固定版本的独立二进制引入，不作为 pip 依赖**：mitmproxy ≥ 11.1.0 要求 Python ≥ 3.12，而 agent-sec-cli 锁定 3.11.6；上游 PyInstaller standalone 构建自带解释器，因此固定二进制既解开了版本冲突，也让 mitmproxy 从 `uv.lock` / `requirements.txt` 中彻底消失（连带消掉 `mitmproxy_rs` 的 cp311 wheel 兼容风险）。

版本、URL 与 SHA256 固定在 `scripts/secret-gateway/mitmproxy-provenance.toml`，由 `scripts/secret-gateway/prepare-mitmproxy.sh` 下载、校验并安装 `mitmdump`（两处常量在脚本启动时互相交叉校验，不一致直接 `die`）。它放在 `scripts/` 而不在 `packaging/` 下，是因为它属于**部署侧**行为：本组件有两条打包出口（anolisa CLI 预构建打包、RPM），而这个脚本对两者都适用，也允许运维在主机上直接执行，因此不归属于任何单一出口。

由此产生一条必须遵守的连带约束：**addon 由 mitmproxy 自带解释器加载，不能 import `agent_sec_cli`**，只能用 stdlib + mitmproxy API。需要项目逻辑的部分经 Unix socket 交给 daemon。这正好是想要的形态——addon 极薄，数据面将来可替换成 Rust/Go 而策略面原样复用。

## 5. 组件与职责

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| 注入 addon | `agent_sec_cli/gateway/credential_inject_addon.py` | `request` hook 做 fake→real 替换；`response`/`error` hook 记录并上报。**stdlib-only** |
| proxy 托管 job | `agent_sec_cli/daemon/jobs/secret_gateway.py` | 拉起并监管 mitmdump 子进程（退避重启、随 daemon 优雅退出） |
| daemon 方法 | `agent_sec_cli/daemon/secret_gateway_methods.py` | `gateway.status` 只读状态；`gateway.audit` 接收 addon 上报并落审计 |
| fake token | `agent_sec_cli/gateway/fake_token.py` | 从真凭据推导同构的占位凭据 |
| 二进制安装 | `scripts/secret-gateway/prepare-mitmproxy.sh` | 下载、校验 SHA256、安装 `mitmdump` |
| 配置模板 | `scripts/secret-gateway/config.json.example` | 供运维抄一份手写 |

以下两个是**测试专用**，住在 `tests/e2e/secret-gateway/`、不随包部署（一个会覆盖 `/etc` 配置，一个会回显凭据，都不应出现在生产主机上）：

| 工具 | 职责 |
| --- | --- |
| `bootstrap.py` | 生成测试用配置（fake token + 内联 inert 真凭据） |
| `mock_echo_upstream.py` | 回显收到的 `Authorization` / query，用于断言注入后的出站值 |

单测在 `tests/unit-test/gateway/`：用桩模拟 mitmproxy 的请求对象，不需安装 mitmproxy 即可验证注入、配置校验与日志脱敏。

## 6. 端到端数据流

1. **配置（一次性）**：真凭据、fake token 与允许的 host 写进 root 域的配置文件。
2. **注入 fake token**：Agent 侧只拿到 fake token（环境变量）。
3. **Agent 发起请求**：用 fake token 组装正常 HTTPS 请求。
4. **强制转发**：请求被内核层规则改道进 proxy，Agent 无感、无法绕过。
5. **TLS 终结**：proxy 用自签 CA 为目标域动态签发证书，终结来自 Agent 的 TLS，在明文层处理。
6. **命中判定**：host 在该凭据的 hosts 内、且 `Authorization` 头含 fake token → 命中；否则 passthrough 不改。
7. **凭据替换**：把头里的 fake token **子串替换**为真 token。子串替换而非重写整个头，是为了同时兼容 `Bearer <fake>` 与 `token <fake>` 两种形态。
8. **重新发起**：proxy 作为客户端对真实 endpoint 重建 TLS 连接并发出请求。
9. **审计**：addon 经 `gateway.audit` 上报（host / 凭据 id / 是否命中 / 是否注入 / 状态 / 耗时），由 **daemon 侧**写入统一的 `security_events`（JSONL + SQLite），可用 `agent-sec-cli events --category secret_gateway` 查询。IPC 不可用时降级为只写本地 `proxy.log`，不阻塞转发。

全程 Agent 上下文里只出现 fake token；真 token 仅在 proxy 进程内短暂存在，且日志中只以掩码与 sha256 前缀出现。

## 7. 配置与其消费时机

部署期**手写** `/etc/agent-sec/gateway/config.json`（模板在 `scripts/secret-gateway/config.json.example`），网关在**启动时一次性消费**它：mitmdump 拉起 addon → addon 的 `running` hook 读配置、校验权限、把凭据读入内存。无热加载，改完需重启。

一条凭据绑定三件事：`fake_token`（给 agent 的占位值）、`real_token`（**真凭据本体，内联在配置里**）、`hosts`（允许注入的目标）。字段语义、报错对照表与多凭据示例见 `SECRET_GATEWAY_CONFIG_zh.md`。

真凭据内联是为了部署简单（一个文件、一次编辑），代价是**配置文件本身就是密钥文件**。因此启动时强制校验它属主为 root 且无任何 group/other 位，不满足则**拒绝启动**。两层理由：

- **可读 → agent 直接拿到真凭据**。注意单看 `0600` 不够：属主若是 agent 的 uid，`0600` 对它照样可读。
- **可写 → agent 能往 `hosts` 里加一个自己控制的域名**，然后拿 fake token 发过去，网关会把真 token 注入并发往攻击者。

还有一条不属于技术控制的风险：配置文件比密钥文件更容易被人顺手复制（贴工单、贴聊天、误提交进 git）。文档里已就此给出明确运维要求；若后续需要对接 Vault / K8s Secret，再引入一个凭据源抽象（如 `credential_source`）比现在保留两套写法更干净。

## 8. 强制出网

方案要成立的前提是 Agent 出站流量**无旁路**地全部经过 proxy。仅靠 `HTTPS_PROXY` 环境变量不够——那是自愿遵守的约定，部分客户端直接忽略，Agent 也能自行 unset 绕过。因此强制下沉到内核：

```
iptables -t nat -A OUTPUT -p tcp --dport <port> \
    -m owner --uid-owner <agent uid> \
    -m comment --comment "agent-sec-gateway:uid=<N>:dport=<P>:to=<LP>" \
    -j REDIRECT --to-ports <listen_port>
```

这条规则的关键性质：

- **按 uid 精确命中**：只劫持 agent 的流量，不影响宿主上其它进程。
- **proxy 自己的上游连接不被重定向**：proxy 跑在 root uid，不符合 `--uid-owner <agent uid>`，天然避免环路。
- **不绑定高版本内核**：只用 iptables nat + owner match（2.6.28+ 即有），显式不用 TPROXY / eBPF / cgroup v2 connect hook。

### daemon 自动部署流量劫持

运维在 `config.json` 里写 `forwarding` 段（agent uid、端口列表、模式），然后起 daemon：

1. daemon job 读配置 → `ForwardingPolicy`
2. `NatRuleManager.reconcile()`：先按 comment marker 清理自己遗留的旧规则，再装新规则
3. 拉起 mitmdump
4. 停止时 `teardown()` 自动撕掉规则

配置字段详见 `SECRET_GATEWAY_CONFIG_zh.md` 的 `forwarding` 段。

## 9. 安全与崩溃防线

### 9.1 配置校验

| 检查 | 原因 |
|---|---|
| `agent_uid == 0` 拒绝 | agent 不能是 root，否则与网关共信任域——能读配置（拿到真凭据）、能改 iptables（绕过劫持） |
| `listen_port` 不能出现在 `ports` 里 | 否则网关自己的出站也被重定向回自己（死循环） |
| 配置文件必须 root 属主、无 group/other 位 | 可读 → 凭据泄漏；可写 → agent 往 hosts 加自控域名骗网关注入 |
| `agent_uid` / `agent_user` 互斥 | 两个来源同时存在会静默漂移 |
| `location=query` 与 `header` / `param` 不得矛盾 | 宁可报错，不静默忽略你明确写下的字段 |

### 9.2 崩溃残留

daemon 被 kill -9 时 iptables 规则会残留：流量被重定向到一个已经不存在的监听者，表现为诡异的网络故障而非网关故障。

防线：每条规则带**唯一 comment 标记**（`agent-sec-gateway:uid=<N>:dport=<P>:to=<LP>`）：

- **识别自己的**：reconcile 只删带该标记的规则，不会误删其它系统的规则。
- **启动时自动清理**：daemon 重启时 `reconcile()` 先删旧的再装新的，无需人工介入。
- **标记编码了策略参数**：策略变更（换 uid、换端口）产生不同的标记，旧策略的残留规则会被新启动可见地识别为「已过期」并删除。
- **手动清理**（daemon 不在了的紧急情况）：`iptables -t nat -S OUTPUT | grep agent-sec-gateway | sed 's/-A/-D/' | xargs -L1 iptables -t nat`。

### 9.3 半装回滚

安装过程中任何一条规则失败，立即 `_remove_marked()` 清掉已装的那几条。不留「只劫持部分端口」的诡异状态——那等于告诉 agent「443 得经网关，但 80 可以直连」，攻击者用 80 即可绕过。

### 9.4 iptables -S 的引号坑

`iptables -S` 输出的 comment 带双引号（`--comment "agent-sec-gateway:..."`）。用 `str.split()` 解析会把引号当成 comment 内容的一部分，导致删除时匹配不上（规则永远清不掉）。必须用 `shlex.split()`，它会正确剥掉引号。

### 9.5 `manage_rules=false`

有些环境的 iptables 由外部系统统一管理（k8s、防火墙管理器），daemon 去插规则可能打架。设为 `false` 时 daemon 只拉 proxy，不碰 iptables，规则交运维手动装。此时运维有责任确保规则存在且指向 `listen_port`。

### 9.6 query 形式凭据的日志泄漏防护

`location=query` 时注入后的真凭据会进入 URL，而 URL 到处被记日志。两层防护不得去掉：

1. addon 写日志/上报审计前对 `path` 与 `error` 做 `_scrub()`（把真凭据替为 `<凭据id:redacted>`）；
2. job 拉起 mitmdump 带 `--set flow_detail=0`（否则 mitmproxy 内置 dumper 会把含真凭据的完整 URL 打进 `gateway.log`）。

## 10. 边界与开放问题

- **CA 信任分发**：TLS 终结要求 Agent 信任 proxy 的自签 CA，这张 CA 的信任范围限定与生命周期管理需专门设计。
- **证书 pinning 客户端**：做 pinning 或强制校验证书链的 SDK 会拒绝网关签发的证书，需评估影响面与兼容策略。
- **非 HTTP 协议**：不走 HTTP 的协议不适用「解析请求、替换头」模型。
- **响应侧脱敏**：本期无入站回替，若上游在响应体或错误信息里回显凭据，真 token 会绕一圈回到上下文。
- **信任核心自身**：proxy 与 daemon 一旦被攻陷凭据即失守，其攻击面收敛与接口鉴权是独立议题。
- **配额与滥用**：网关持有真实凭据即继承其账单，需要配额与异常调用检测。
- **性能**：全量出站经 proxy 并做 TLS 终结的延迟与资源开销待实测。
