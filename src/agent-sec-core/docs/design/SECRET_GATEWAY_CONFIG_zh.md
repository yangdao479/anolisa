# Secret Gateway 配置说明（手写配置）

网关**在启动时一次性消费**这份配置，配置有两类消费者：

1. **daemon**（job 启动时）：读 `forwarding` 段 → 自动安装 iptables 流量劫持规则 → 拉起 mitmdump。
2. **addon**（mitmdump 拉起后的 `running` hook）：读 `credentials` 段 → 校验权限 → 把凭据读入内存。

改完配置需要重启网关才生效（无热加载）。

## 唯一的配置文件

`/etc/agent-sec/gateway/config.json`（路径可用环境变量 `AGENT_SEC_GATEWAY_CONFIG` 覆盖）

fake token 与**真凭据本体**都写在这一个文件里。**因此这个文件就是密钥文件**，权限要求：

| 要求 | 说明 |
|---|---|
| **属主必须是 root** | 只看权限位不够：`0600` 但属主是 agent 的 uid，agent 照样能读 |
| **不得有任何 group/other 位** | 推荐 `0600` |

网关启动时强制校验这两条，**不满足直接拒绝启动**。原因有两层：

- **可读 → agent 直接拿到真凭据**（凭据就在文件里）；
- **可写 → agent 能往 `hosts` 里加一个自己控制的域名**，然后拿 fake token 发过去，网关会把真 token 注入并发往攻击者。

### `0600` 不会妨碍你修改它

`0600` = **属主可读可写**，而属主就是 root，所以 root 随时可以直接编辑（何况 root 本身就绕过 DAC 写权限检查）。校验只拒绝 group/other 位，**不限制属主读写**。

真正会碍事的是 `0400`（属主只读）：虽然 root 仍能强制写入，但 `vim` 会提示只读、`>` 重定向会被 shell 拒绝。**所以推荐用 `0600`**，它同时满足安全校验与可维护性。

典型的“改配置 → 重启网关”循环：

```bash
vim /etc/agent-sec/gateway/config.json     # 0600 root 下直接改
stat -c '%a %U' /etc/agent-sec/gateway/config.json   # 确认仍是 600 root
pkill -f 'mitmdump --mode' || true       # 停掉旧的
# 再按下面「手动启动网关」重新拉起（或重启 daemon）
```

注意部分编辑器（包括 `vim` 默认行为）会用“写新文件 + rename”的方式保存，新文件的权限受 umask 影响。改完须用上面的 `stat` 确认仍是 `600 root`，否则网关下次启动会拒绝。

### 手动启动网关并消费本配置

不依赖 daemon，root 可以直接跑：

```bash
export TMPDIR=/var/lib/agent-sec/tmp          # PyInstaller 自解包需可执行目录
export AGENT_SEC_GATEWAY_CONFIG=/etc/agent-sec/gateway/config.json
mkdir -p "$TMPDIR" /var/log/agent-sec

/opt/agent-sec/bin/mitmdump \
  --mode transparent --listen-host 0.0.0.0 --listen-port 18080 \
  -s /opt/agent-sec/lib/.../gateway/credential_inject_addon.py \
  --set confdir=/etc/agent-sec/gateway/mitm-ca \
  --set block_global=false \
  --set flow_detail=0
```

启动后确认配置确实被消费：

```bash
grep "secret gateway config loaded" /var/log/agent-sec/gateway.log
grep "secret gateway credential ready" /var/log/agent-sec/gateway.log
```

后者每条凭据一行，带 `carrier=header:Authorization` 或 `carrier=query:access_token`，且 fake/real 只以掩码 + sha256 出现。

**调试时想看到真实换上的 token**：把 `--set flow_detail=0` 改为 `--set flow_detail=2`，mitmproxy 会打出完整请求行（query 形式下就能直接看到注入后的 `access_token=<真值>`）。配合测试用的 mock echo 上游（带 `--show-authorization`）可以从上游视角看到完整的 `authorization` / `query` / `full_path`。

> 两个开关都是**调试专用**，会把真凭据写进日志或响应体；对真凭据调试完毕后记得改回 `flow_detail=0` 并清理日志。addon 自己的结构化日志（`secret_gateway_flow`）始终会把真凭据替成 `<凭据id:redacted>`，这一层不可关。

> **运维须知**：真凭据内联在配置里，所以这个文件不能像普通配置那样对待——**不要**贴进工单/聊天、**不要**提交进 git、**不要**随备份外流。排障时如果要给别人看配置，先把 `real_token` 的值抹掉。

## 最小可用配置

```bash
install -d -m 0755 /etc/agent-sec/gateway

# umask 保证创建瞬间就不是 group/other 可读，不留可读窗口
( umask 077 && cat > /etc/agent-sec/gateway/config.json <<'EOF'
{
  "schema_version": 1,
  "log_path": "/var/log/agent-sec/gateway.log",
  "daemon_socket": "/run/agent-sec-core/daemon.sock",
  "credentials": [
    {
      "id": "github-token",
      "fake_token": "ghp_0000000000000000000000000000AGENTSEC",
      "real_token": "ghp_你的真实 github token",
      "hosts": ["api.github.com"]
    },
    {
      "id": "gitee-token",
      "fake_token": "REPLACEME000000000000000AGENTSEC",
      "real_token": "你的真实 gitee token",
      "hosts": ["gitee.com"],
      "location": "query",
      "param": "access_token"
    }
  ]
}
EOF
)
chown root:root /etc/agent-sec/gateway/config.json
chmod 0600 /etc/agent-sec/gateway/config.json
stat -c '%a %U %n' /etc/agent-sec/gateway/config.json   # 期望 600 root
```

两条凭据正好覆盖两种携带方式：GitHub 走 `Authorization` 请求头（默认），Gitee 走 `access_token` query 参数。

然后把各自的 `fake_token` 交给 agent（例如分别设为 `GITHUB_TOKEN` 与 `GITEE_TOKEN`）。**agent 侧只给 fake，不要给真的。**

## 字段说明

顶层：

| 字段 | 必填 | 默认 | 消费者 | 说明 |
|---|---|---|---|---|
| `forwarding` | 是 | — | daemon | 流量劫持策略（mode/agent/ports/manage_rules） |
| `credentials` | 是 | — | addon | 凭据数组，不能为空 |
| `log_path` | 否 | `/var/log/agent-sec/gateway.log` | addon | 网关流水日志 |
| `daemon_socket` | 否 | 空 | addon | daemon socket。留空则审计降级为只写本地日志 |
| `audit_timeout_ms` | 否 | `800` | addon | 上报审计的超时 |
| `schema_version` | 否 | — | — | 目前**不被校验**，仅预留 |

### `forwarding` 段（daemon 消费）

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `mode` | 否 | `uid` | `uid` = root netns 按 uid 劫持（daemon 全自动部署） |
| `agent_user` | 二选一 | — | agent 的 Unix 用户名。解析为 uid 后即固定（passwd 变更不会追踪） |
| `agent_uid` | 二选一 | — | 直接填 uid。与 `agent_user` 互斥，两个都写会拒绝启动 |
| `listen_port` | 否 | `18080` | mitmdump 监听端口 |
| `ports` | 否 | `[80, 443]` | 要劫持的出站端口列表 |
| `manage_rules` | 否 | `true` | daemon 是否代管 iptables 规则。设 `false` 时只拉 proxy，规则交运维 |

**`agent_uid=0` 会被拒绝**：agent 不能是 root，否则它与网关共信任域——能读这份配置、能改 iptables 规则。

**`listen_port` 不能出现在 `ports` 里**：否则网关自己的出站也被重定向回自己（死循环）。

#### mode 的区别

### 凭据在 query 参数里的写法

有些服务把凭据放在 URL query 而不是请求头（常见的有 Gitee 的 `access_token`、Google API 的 `key`），用 `location: "query"` + `param`：

```json
{
  "id": "gitee-token",
  "fake_token": "REPLACEME000000000000000AGENTSEC",
  "real_token": "真实值",
  "hosts": ["gitee.com"],
  "location": "query",
  "param": "access_token"
}
```

行为细节（已经本地用桩验证）：

- **只换该参数的值**，`?page=2&access_token=X&per_page=5` 里的其余参数原样保留；
- 同一参数**重复出现**时每一份都会被换；
- URL 重新编码由 mitmproxy 完成，无需手工处理转义。

> **一个重要的连带影响**：query 形式下，注入后的**真凭据会出现在 URL 里**，而 URL 是会被记日志的。为此做了两层处理：① addon 写日志/上报审计前会把路径里的真凭据替成 `<凭据id:redacted>`（这一层不可关）；② 启动 mitmdump 时带 `--set flow_detail=0`，否则 mitmproxy 自带的 dumper 会把完整 URL（含真凭据）打到 stdout → `proxy.log`。
>
> 因此：**平时跑用 `flow_detail=0`；只有在有意调试、且凭据是 inert 测试值时才改成 `flow_detail=2`**（见上面「手动启动网关」一节）。

## 匹配语义（务必理解）

对每个出站请求，网关按顺序判断：

1. 请求 host **在某条凭据的 `hosts` 里**，且该条凭据的携带位置（`header` 或 `query` 参数）的值里**包含该条的 `fake_token`** → 把值中的 fake token **子串替换**为 `real_token` 后发出。子串替换意味着 header 形式下 `Bearer <fake>` 与 `token <fake>` 两种写法都能覆盖，query 形式下其余参数也不受影响。
2. 否则 → **原样放行，不做任何改写**。

第 2 条要特别注意：**未命中不等于拒绝**。当前版本没有「携带受管凭据却发往白名单外 host 就阻断」的逻辑，也**不支持按 URL path 限定**（`hosts` 只到主机名）。这两项都是已知的非目标，见 `SECRET_GATEWAY_zh.md` 第 2 节。

## 多凭据示例（不限 GitHub）

注入机制本身不认识任何具体厂商——发往哪些 host、用哪个请求头、换哪一对值，全部来自配置。**新增一把 API key 是改配置，不是改代码。**

```json
{
  "credentials": [
    {
      "id": "github",
      "fake_token": "ghp_0000000000000000000000000000AGENTSEC",
      "real_token": "ghp_真实值",
      "hosts": ["api.github.com"]
    },
    {
      "id": "anthropic",
      "fake_token": "sk-ant-api03-000000000000000000000AGENTSEC",
      "real_token": "sk-ant-api03-真实值",
      "hosts": ["api.anthropic.com"],
      "header": "x-api-key"
    },
    {
      "id": "openai",
      "fake_token": "sk-proj-00000000000000000000000AGENTSEC",
      "real_token": "sk-proj-真实值",
      "hosts": ["api.openai.com"]
    },
    {
      "id": "azure-openai",
      "fake_token": "00000000000000000000000000AGENTSEC",
      "real_token": "真实值",
      "hosts": ["my-resource.openai.azure.com"],
      "header": "api-key"
    },
    {
      "id": "internal-service",
      "fake_token": "internal_000000000000000AGENTSEC",
      "real_token": "internal_真实值",
      "hosts": ["api.internal.example.com"],
      "header": "X-Service-Token"
    }
  ]
}
```

要点：

- **不同厂商用不同请求头**，靠 `header` 字段表达（Anthropic 是 `x-api-key`、Azure OpenAI 是 `api-key`、内部服务可能是自定义头）。默认 `Authorization`。
- **同一 host 可以有多把凭据**，只要 `fake_token` 不同即可区分。
- **一把凭据可以跨多个 host**（`hosts` 是数组）。

## fake token 怎么取值

唯一硬要求：**与真凭据同构**。否则 agent 或其客户端可能因格式异常而根本不发请求，网关就等不到注入时机。同构指：长度相同、前缀相同、字符集相同。

推荐直接从真凭据推导（适用于任意厂商，不限于 GitHub）：

```bash
python3 -c "
from agent_sec_cli.gateway.fake_token import generate_fake_token_like as g
print(g('粘你的真实凭据'))"
```

它会保留前缀（`ghp_`、`sk-ant-api03-`、`xoxb-`…）与长度、字符类型，尾部带 `AGENTSEC` 标记。

**一个例外**：前缀里没有分隔符的厂商（最典型是 Google 的 `AIza…`）无法自动识别，需显式指定保留几个字符：

```bash
python3 -c "
from agent_sec_cli.gateway.fake_token import generate_fake_token_like as g
print(g('AIzaSy真实值', keep_prefix=4))"
```

手写时想自查是否同构：

```bash
python3 -c "
from agent_sec_cli.gateway.fake_token import same_shape
print(same_shape('真凭据', 'fake凭据'))"
```

注意 `same_shape` 只能校长度/分隔符前缀/字符类，它不知道 `AIza` 对 Google 有特殊含义，这类前缀需要人眼确认。

## 启动时的报错对照

配置错误一律**拒绝启动**（安全组件不能在输入不可信时带病运行）。报错在 `log_path` 与 mitmdump 的 stdout：

| 报错 | 原因 |
|---|---|
| `config ... does not exist` | 路径不对，或需要设 `AGENT_SEC_GATEWAY_CONFIG` |
| `is owned by uid N, expected root (0)` | 属主不是 root，`chown root` |
| `has mode 0644; group/other access must be removed` | 权限太宽，`chmod 600` |
| `is not valid JSON: ... (line L, column C)` | JSON 语法错，按行列定位 |
| `requires a non-empty "credentials" array` | 缺 `credentials` 或为空 |
| `credentials[0] requires a non-empty "id"` | 该项缺 `id`（按数组下标定位） |
| `credentials[0] ("x") requires "real_token"` | 缺真凭据 |
| `credentials[0] ("x") requires "hosts" as a non-empty list` | `hosts` 缺失或不是非空数组 |
| `has unsupported "location" "body"` | `location` 只能是 `header` 或 `query` |
| `requires "param" when location is "query"` | query 形式必须给 `param` |
| `sets "header" but location is "query"` | 两个字段矛盾，去掉一个 |
| `sets "param" but location is "header"` | 同上 |
| `"real_token" and "fake_token" are identical` | 两者相同，注入等于空操作 |
| `credentials "a" and "b" share the same fake_token` | 两条凭据 fake token 重复，注入会变得不确定 |

## 验证已生效

```bash
grep "secret gateway config loaded" /var/log/agent-sec/gateway.log
grep "secret gateway credential ready" /var/log/agent-sec/gateway.log
```

预期后者每条凭据一行，且 fake/real 都只以 `ghp_***[len=40,sha256=...]` 形式出现——**日志里不会有明文**。想确认没有泄漏，可以直接反向搜：

```bash
grep -c "$(python3 -c "import json;print(json.load(open('/etc/agent-sec/gateway/config.json'))['credentials'][0]['real_token'])")" \
  /var/log/agent-sec/gateway.log   # 期望 0
```

## 与 bootstrap.py 的关系

`python3 -m agent_sec_cli.gateway.bootstrap` 只是**测试环境**的便利脚本：生成 fake token、内联一把 inert 的"真"凭据、整体覆盖 config.json。它不适合生产：

- `--real-token` 会让真凭据出现在命令行参数里（进 shell history、`ps` 输出对同机用户短暂可见）；
- 它整体覆盖配置而不是增量编辑。

生产环境按本文手写配置。
