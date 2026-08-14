# Secret Gateway — 引入前后的用户使用差异

面向部署运维/最终用户，只讲**为了把 agent 跑起来需要做的事**如何改变。不讨论内部实现细节、故障模式、性能等（那些在 `SECRET_GATEWAY_zh.md`）。

---

## 一句话对比

三种部署形态从左到右递增：

- **A. 只跑 agent**：普通用户能完成。
- **B. + sec-core 其他能力**（code scan / prompt scan / 审计 hook，但不启用 gateway）：多装一个包，daemon 以 user unit 自动跑起来，agent 启动方式不变。
- **C. + 启用 gateway**：多一套 root 侧部署（agent 用户、config、CA、root daemon），且多出一条“agent 只给 fake、不给 real”的运维纪律。

关键分界在 **B→C**：A→B 基本无感，B→C 才是部署复杂度的跳变。

## 部署步骤对照

### A. 直接跑 agent（4 步）

```
1. 装 agent 本体
2. 拿一个 API token
3. export TOKEN=ghp_xxx     （或写进 agent 配置）
4. 起 agent
```

普通用户可完成。

### B. 加上 sec-core 但不用 gateway（5 步）

适用于只想用 sec-core 的其他能力（代码扫描 hook、prompt 扫描、security events 审计），不想引入凭据代持。

```
1. 装 agent 本体
2. 装 sec-core
3. systemctl --user enable --now agent-sec-core.service
   （user 级 daemon，普通用户自己跑）
4. 拿 token；export TOKEN=ghp_xxx
5. 起 agent（普通用户、无需 root）
```

相对 A 只多了“装包 + 一行 systemctl”；**agent 侧的启动流程完全不变**。

> `AGENT_SEC_GATEWAY_ENABLED` 默认不设，就不会注册 gateway job——daemon 运行时只服务其他 hook，不碰凭据。

### C. 加上 sec-core 且启用 gateway（8 步，**必须 root**）

```
1. 装 agent 本体
2. 装 sec-core
3. 建 agent 用户（不能是 root）
   sudo useradd -m agentuser
4. 拿真 token
5. 写 /etc/agent-sec/gateway/config.json
   - forwarding.agent_user = "agentuser"
   - credentials[*].fake_token / real_token / hosts
   sudo chown root:root; sudo chmod 600 …
6. **以 root 起 daemon**（不走 user unit），带 AGENT_SEC_GATEWAY_ENABLED=1
   （daemon 会自动把网关 CA 装进系统信任库）
7. 若 agent 是 Python / Node / Java，额外设一个环境变量（见下文）
8. 起 agent（**必须以 agentuser 身份跑**）
   sudo -u agentuser sh -c 'export TOKEN=<fake_token>; ./agent'
```

相对 B 新增的都集中在“root 侧部署面”与“agent 启动契约”。

> 为什么不能用 user unit：gateway job 需要改 iptables、读 `0600 root` 的 config，都需 root。当前装包自带的 user unit 带了 `NoNewPrivileges` / `ProtectKernelModules` 等 hardening，在它里开 gateway 会直接失败。B 里那条 systemctl --user 可以保留（服务其他 hook），也可以关掉。

## 已知边界（现阶段）

- **同一主机上若既要 B 又要 C**，目前需要两实例 daemon（user 一个、root 一个）共存；C 的 root daemon 接管 gateway，B 的 user daemon 仍服务其他 hook。后续可能提供官方的 root system unit 以避免手写启动命令。
- gateway 在 user daemon 里开不会造成静默失败：config 是 `0600 root`，user daemon 读不到，job 会不启动并在 `gateway.status` 里报错。

## 新增的责任

| 责任 | 说明 |
|---|---|
| **root 权限** | 写 `/etc/agent-sec/gateway/`、起 daemon、装 iptables 规则都需要 root |
| **agent 用户预先存在** | config 里 `agent_user` 必须能被 `getpwnam` 解析 |
| **agent 以指定用户跑** | 跑错用户流量不被劫持；跑成 root 会被 daemon 拒绝 |
| **准备两份 token** | 真 token 写 config、fake token 给 agent |
| **给 agent 的必须是 fake** | 这是**运维纪律**，不是技术保证——如果不小心把真 token 给了 agent，gateway 看到"header 里的 token 不是我期望的 fake 值"会 passthrough，真 token 就原样出去了 |
| **Python / Node / Java 需设环境变量** | 见下一节；其他运行时（curl / Go）daemon 已自动处理 |

## CA 信任：daemon 自动做了什么、还剩什么

网关终结 TLS，所以 agent 必须信任网关自签的 CA，否则一律报 `CERTIFICATE_VERIFY_FAILED`。

**daemon 启动时自动完成的**：

- 把 CA 装进系统信任库（自动区分 RPM 系 / Deb 系，不需要你关心路径）
- 把 CA 公钥放到 agent 可读的 `/opt/agent-sec/gateway/ca-cert.pem`
- daemon 停止时自动移除

所以这些客户端**零配置就能用**：curl、wget、Go `net/http`、部分 Rust HTTPS 库。

**仍需你手动做的**（这些运行时自带 CA bundle，不读系统信任库）：

| agent 类型 | 加一行 |
|---|---|
| Python（requests / httpx） | `export SSL_CERT_FILE=/opt/agent-sec/gateway/ca-cert.pem` |
| Node.js | `export NODE_EXTRA_CA_CERTS=/opt/agent-sec/gateway/ca-cert.pem` |
| Java | `-Djavax.net.ssl.trustStore=...`（或改 keystore） |
| 证书 pinning 的 SDK | **用不了**——pinning 就是为了拒绝中间人证书 |

为什么 daemon 不能把这几个 env 也自动弄好：它无法往一个**尚未启动、且可能以任意方式（shell / systemd / docker / k8s）拉起**的进程里注入环境变量。这是操作系统的边界，不是实现选择。

**排障**：`gateway.status` 里的 `ca_install_status` 会告诉你 CA 到底装没装上（`installed` / `skipped:...` / `failed:...`）。

## 收益（对应付出）

只有一条：**真凭据不进 agent 的内存、日志、配置文件、进程环境**。真 token 只在 gateway 进程内短暂存在。

## 什么时候值得付出这个部署成本

**值得**：

- agent 是不可信代码（跑用户上传的 prompt、第三方 skill、社区插件）
- 凭据本身高价值（GitHub PAT with write、云厂商 keys、支付 API）
- 需要集中运维凭据轮换（换 token 时只动 gateway config，不用重发 agent）

**不值得**：

- agent 是自己写自己跑，真 token 放 `.env` 与放 gateway config 风险等价
- 场景只有单一 host、单一凭据

## 唯一容易踩的坑

> "我设置了 gateway 但 agent 还能直接用真 token"

这不是 bug，是配置错误。gateway 只在看到 `header 值里含 fake_token` 时才做替换；如果运维图省事把真 token 也告诉了 agent，gateway 看到的是"不是我认识的 fake 值 → passthrough"，真 token 就出去了。

**因此这套方案的安全性依赖一条纪律**：agent 侧永远只给 fake，永远不给 real。
