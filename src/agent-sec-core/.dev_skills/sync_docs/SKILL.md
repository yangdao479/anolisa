---
name: sync_docs
version: 0.3.0
description: 接收用户的 design 更新指令，端到端完成目标 design 文档更新及所有下游文档（related designs、guide、user_guide.md）的连锁同步。guide 正确性为第一优先级。
---

# sync_docs

## 触发条件

当以下任一情况发生时使用本技能：
- 用户要求更新某个（或多个）design 文档的内容
- 用户要求为某个模块补充设计文档中缺失的章节
- 用户显式要求同步文档

## 目标

从用户的更新指令出发，端到端完成整条文档链路的更新：

```
用户指令 → 更新目标 design → 传播到 related designs → 同步所有 consumed_by guide → 审计 user_guide.md
```

确保一次指令完成后，文档体系（design → guide → user_guide.md）全链路一致。

**第一优先级：guide 文档内容的正确性。** guide 面向用户，任何不正确的命令、参数、行为描述都会误导用户。

## 核心原则

1. **正确性优先**：guide 中描述的 CLI 命令、参数、输出格式、行为语义必须与当前代码完全一致。当 design 文档与代码存在冲突时，以代码为准。
2. **最小变更**：仅更新实际受影响的内容段落，不做无关改动。
3. **用户视角**：guide 从用户如何使用出发，不暴露内部实现细节。

## 数据源优先级

当多个来源信息冲突时，按以下优先级取信：

```
代码实现（cli.py、*.py、*.ts、*.rs）
  > design/architecture.md（架构意图）
  > guide 当前内容（可能已过时）
```

## 执行流程

### Phase 1: 理解意图与更新目标 design

1. **解析用户指令**

   确定：
   - 目标 design 模块（一个或多个）
   - 需要补充/修改的内容主题
   - 涉及的范围（单模块 or 跨模块同类内容）

   若用户指令涉及多个 design（如「所有 agent hook 的 design 都补充配置说明」），逐一列出所有目标模块。

2. **收集 ground truth**

   从代码实现中提取事实依据（design 内容必须以代码为准）：
   - 读取目标模块 `_meta.yaml` 的 `code_scope` → 定位源码
   - 读取源码中相关的实现细节（配置加载、参数定义、行为逻辑等）
   - 读取 `related_designs` 中列出的关联模块，了解上下文

3. **更新目标 design 文档**

   基于代码 ground truth，在目标 design 的 `architecture.md` 中补充/修改对应章节。
   - 内容必须与代码实现一致
   - 遵循 design 文档的现有结构和风格
   - 若多个模块需要同类更新（如都需要「配置」章节），每个模块都要独立完成

4. **检查 related_designs 是否需要联动更新**

   读取目标模块 `_meta.yaml` 的 `related_designs`：
   - 若本次变更涉及模块间的接口/契约 → 检查关联模块的 design 是否需要同步修改
   - 若仅是模块内部文档补充 → 跳过

### Phase 2: 变更影响分析

基于 Phase 1 中已更新的 design 文档，确定下游影响范围：

1. **汇总所有已更新的 design 模块**

2. **正向追踪：读取每个已更新模块的 `consumed_by` 字段**，合并去重得到初始受影响 guide 列表

3. **反向影响扫描：检查 `related_designs` 中关联模块的 design 是否也受影响**

   对每个已更新模块：
   - 读取其 `_meta.yaml` 的 `related_designs` 字段
   - 判定：本次变更内容是否描述了关联模块的配置项或行为语义
     - 判定依据：更新文本中出现了关联模块所属的配置字段名、环境变量名、或行为说明
   - 若命中：将该关联模块标记为「受影响 design」，读取其 `consumed_by`，将**全部** consumed_by 目标加入影响清单

   **逻辑链路**：`related_designs` → 关联模块的 design 受影响 → 读取其 `consumed_by` → 下游 guide 纳入影响清单

   **典型触发场景**：更新 observability design 时描述了 code_scanner 产生的事件字段（如 `event_type`、`severity`）→ 这些字段属于 code_scanner 模块 → code_scanner design 受影响 → 读取 code_scanner 的 consumed_by → guide/code_scanner.md 等纳入影响清单

   **不触发的场景**：仅更新模块自身的内部架构描述，未涉及关联模块的配置/行为

4. **合并去重，输出完整影响清单**

   ```
   所有受影响的 design 模块（直接更新 + 反向命中）→ 各自的 consumed_by → 合并去重得到完整 guide 列表
   ```

   示例 1（仅正向追踪）：
   ```
   design/code_scanner → guide/code_scanner.md, guide/hermes_plugin.md, guide/openclaw_plugin.md, guide/codex_plugin.md, guide/cosh_extension.md
   design/prompt_scanner → guide/prompt_scanner.md, guide/hermes_plugin.md, guide/openclaw_plugin.md, guide/codex_plugin.md, guide/cosh_extension.md
   合并后：code_scanner.md, prompt_scanner.md, hermes_plugin.md, openclaw_plugin.md, codex_plugin.md, cosh_extension.md
   ```

   示例 2（正向 + 反向影响扫描）：
   ```
   直接更新的 design:
     design/hermes_plugin → consumed_by: guide/hermes_plugin.md
   反向命中的 design:
     hermes_plugin.related_designs: code_scanner, prompt_scanner, pii_checker, ...
     本次变更描述了 code-scan 的 enable_block → code_scanner design 受影响
     本次变更描述了 prompt-scan 的 warning_ttl_seconds → prompt_scanner design 受影响
   读取受影响 design 的 consumed_by:
     design/code_scanner → guide/code_scanner.md, guide/hermes_plugin.md, ...
     design/prompt_scanner → guide/prompt_scanner.md, guide/hermes_plugin.md, ...
   合并去重: hermes_plugin.md, code_scanner.md, prompt_scanner.md, openclaw_plugin.md, ...
   ```

### Phase 3: 验证与更新 guide 文档

**强制要求：必须逐一处理影响清单中的每个 guide 文件，不可跳过任何一个。**

当一个组件 design（如 code_scanner）的 `consumed_by` 列出多个 guide 时，每个 guide 都必须被检查和更新。典型场景：组件 design 新增了「Agent Hook 配置」段落 → 所有消费该组件的 agent guide（hermes_plugin、openclaw_plugin、codex_plugin、cosh_extension）都需要补充对应的配置说明。

对影响清单中的**每个** guide 文件，按以下步骤执行（不可合并、不可跳过）：

1. **读取 guide 的 sources 字段**（frontmatter）确认 def-use 关系双向一致

2. **对比验证**：将 guide 中描述的内容与代码实际实现逐项对比：
   - CLI 命令名和参数（名称、类型、默认值、是否必填）
   - 输出格式（JSON 字段、verdict 值）
   - Hook 触发点和 Matcher
   - 配置项（环境变量、config.toml、plugin.json 字段及其行为说明）
   - 能力矩阵（各运行时的覆盖范围）
   - 版本号

3. **执行更新**：若发现不一致，以代码为 ground truth 修正 guide 内容：
   - 新增的命令/参数 → 补充到 guide
   - 删除的命令/参数 → 从 guide 移除
   - 变更的行为语义 → 修正描述
   - 新增的 Hook 点 → 更新覆盖矩阵
   - 新增的配置选项 → 在对应 guide 的配置段落补充字段说明和行为影响

4. **跨 guide 一致性**：同一配置选项在不同 agent guide 中的描述必须语义一致：
   - 组件 guide（如 code_scanner.md）：描述该配置项的完整语义和行为影响
   - Agent guide（如 hermes_plugin.md、openclaw_plugin.md）：描述该配置项在本 agent 中的配置方式和效果
   - 所有 agent guide 的配置说明覆盖的字段集合必须与实际 config 文件一致

5. **保持 guide 风格一致**：
   - 以典型调用示例为核心
   - 参数说明精简至用户常用选项
   - 不暴露安装后绝对路径
   - 脚本调用使用相对路径

### Phase 4: 审计并更新 user_guide.md

**每次 guide 文档被更新后，都必须审计 `docs/user_guide.md` 是否需要同步更新。**

审计方法：逐一对比 user_guide.md 中的每个章节（核心能力表、运行时表、CLI 树、配置段、能力矩阵、生命周期覆盖表）与 guide 更新后的实际内容是否一致。

需要更新 user_guide.md 的情况：

| 触发条件 | 更新内容 |
|----------|----------|
| 新增 Tier 1 模块 | 添加到能力列表和 guide 索引表 |
| 删除/重命名模块 | 移除或修正对应行 |
| 新增/删除 CLI 子命令 | 更新「CLI 命令一览」树 |
| 新增 Agent 运行时支持 | 更新运行时表和能力矩阵 |
| 版本号变更 | 更新版本声明 |
| 能力矩阵变化（新增/移除运行时覆盖） | 更新对应运行时行 |
| 生命周期 Hook 点变更 | 更新「生命周期覆盖」表 |
| 配置模型变更（新增配置机制、配置范围扩展） | 更新「配置」章节以反映完整的配置体系 |
| guide 中功能描述发生实质变化 | 核查 user_guide.md 中对应模块的概述是否仍准确 |

**「配置模型变更」判定规则：**

user_guide.md 的「配置」章节必须作为所有配置机制的 landing page，完整呈现用户可用的配置体系。以下变更必须同步：
- guide 中新增了新的配置机制（如从仅环境变量扩展到 config.toml）→ user_guide.md 必须提及该配置机制并指向对应 guide
- guide 中配置选项的覆盖范围发生变化（如新增 per-capability 配置）→ user_guide.md 必须反映配置粒度的变化
- guide 中配置的行为语义变更（如从 observe/deny 二态到多策略）→ user_guide.md 的 Verdict/模式说明必须同步

不需要更新 user_guide.md 的情况：
- 仅修改模块内部实现（不影响用户可见行为）
- 仅修改 design 文档内容且 guide 未变化
- guide 中仅修正参数默认值/示例等细节，未改变能力描述或配置范围

### Phase 5: 一致性自检

完成所有更新后执行自检：

1. **完整性校验**：对比影响清单（含正向 + 反向扫描结果）中的 guide 列表与实际已更新/已检查的 guide 列表，确认无遗漏。每个目标都必须有明确的「已更新」或「已确认无需更新」结论
2. **frontmatter 一致性**：每个被更新的 guide 的 `sources` 字段包含所有实际引用的 design 模块
3. **consumed_by 双向校验**：guide 声明的 sources 与对应 design 的 consumed_by 互相匹配
4. **配置覆盖完整性**：各 agent guide 中列出的配置选项必须覆盖该 agent 实际 config 文件中的所有字段
5. **命令可执行性**：guide 中的示例命令在语法上正确（参数名、子命令名与 cli.py 定义一致）
6. **链接有效性**：user_guide.md 中指向 guide/ 的链接路径存在

## 文件定位约定

所有路径均相对于 `src/agent-sec-core/`。

### 固定结构（不随模块增减变化）

| 用途 | 路径模式 |
|------|------|
| 元数据定义 | `docs/design/<module>/_meta.yaml` |
| 设计文档 | `docs/design/<module>/architecture.md` |
| ADR 目录 | `docs/design/<module>/adr/` |
| 用户指南 | `docs/guide/<module>.md`（仅 tier 1） |
| 产品入口 | `docs/user_guide.md` |
| CLI 入口 | `agent-sec-cli/src/agent_sec_cli/cli.py` |

### 模块源码路径（从 _meta.yaml 读取）

各模块的源码位置不在此处列举，执行时从对应模块的 `_meta.yaml` `code_scope` 字段动态获取：

```
读取 docs/design/<module>/_meta.yaml → code_scope 字段即为该模块的所有源码路径
```

这样新增模块时只需创建 `_meta.yaml` 即可，无需修改本 skill。

## 模块分级（从 _meta.yaml 读取）

每个 `docs/design/<module>/_meta.yaml` 包含 `tier` 字段：

```yaml
# _meta.yaml 完整 schema
tier: 1          # 1 = 用户可见（design + guide），2 = 内部架构（仅 design）
code_scope:
  - <路径>/
related_designs: # 与本模块有交互的其他 design 模块（双向标记）
  - <module_name>
consumed_by:     # 哪些 guide 文档消费了本模块的内容
  - guide/<module>.md
```

### Tier 语义

| Tier | 含义 | 文档要求 |
|------|------|----------|
| 1 | 用户可直接操作或感知的能力 | 必须有 `design/` + `guide/` + 在 `user_guide.md` 中索引 |
| 2 | 内部架构实现，用户不直接交互 | 仅需 `design/`，不需要 guide |

### 执行时的 Tier 判定

```
读取命中模块的 _meta.yaml → 取 tier 字段：
  tier: 1 → 检查对应 guide 是否存在且需更新，检查 user_guide.md 索引
  tier: 2 → 仅检查 design 文档，跳过 guide 和 user_guide.md
```

## related_designs 字段的使用

`related_designs` 记录模块间的交互关系，用于变更影响分析的扩展参考：

- **接口变更时**：若模块 A 的对外接口发生变更，检查 `related_designs` 中列出的模块是否受影响
- **架构重构时**：通过 `related_designs` 快速定位所有有交互的模块，评估波及范围
- **与 consumed_by 的区别**：`consumed_by` 是文档层面的"哪些 guide 消费了我的内容"，`related_designs` 是代码层面的"谁和我有调用/被调用关系"

### 新增模块时

1. 创建 `docs/design/<module>/` 目录
2. 编写 `_meta.yaml`（含 tier、code_scope、related_designs、consumed_by）
3. 创建 `architecture.md` 和 `adr/` 子目录
4. 若 `tier: 1`，同时创建 `docs/guide/<module>.md` 并在 `user_guide.md` 中添加索引

## 示例场景

### 场景 A：用户要求为 sandbox design 补充缺失的策略引擎章节

```
用户指令: "sandbox design 缺少策略引擎章节，帮我补上"

Phase 1:
  目标模块: sandbox
  收集 ground truth:
    - 读取 sandbox _meta.yaml 的 code_scope → 定位源码
    - 从源码中提取策略引擎实现（规则加载、匹配算法、判定流程）
  更新 design:
    - sandbox/architecture.md → 新增「策略引擎」章节
  检查 related_designs:
    - hermes_plugin、cosh_extension 与 sandbox 有关联
    - 本次仅补充内部架构文档，无接口变更 → 跳过

Phase 2:
  consumed_by: guide/sandbox.md, guide/hermes_plugin.md, guide/cosh_extension.md

Phase 3:
  逐一检查 3 个 guide：
    - guide/sandbox.md → 若策略引擎揭示了用户可配置的策略规则 → 补充到 guide
    - guide/hermes_plugin.md → 检查 sandbox hook 描述是否需更新
    - guide/cosh_extension.md → 同上

Phase 4:
  策略引擎未引入新 CLI 子命令 → user_guide.md 无需更新

Phase 5:
  完整性校验：3 个 guide 全部有明确结论
  frontmatter 一致性验证
```

### 场景 B：用户要求更新 skill_ledger design 的密钥轮换流程

```
用户指令: "更新 skill_ledger design，补充密钥轮换流程的详细设计"

Phase 1:
  目标模块: skill_ledger
  收集 ground truth:
    - 读取 skill_ledger code_scope 定位源码
    - 从 revoke/rotate 相关代码提取实际实现逻辑
  更新 design:
    - skill_ledger/architecture.md → 新增「密钥轮换」章节
  检查 related_designs:
    - observability 记录 revoke 事件 → 检查是否涉及事件格式变更
    - 若新增了事件类型 → 联动更新 observability design

Phase 2:
  skill_ledger consumed_by: guide/skill_ledger.md, guide/hermes_plugin.md, ...
  若 observability design 也被更新 → 汇总其 consumed_by

Phase 3:
  逐一更新所有受影响 guide：
    - guide/skill_ledger.md → 补充密钥轮换操作步骤和 CLI 示例
    - 其他 agent guide → 检查 skill-ledger 能力描述是否需更新

Phase 4:
  新增 CLI 子命令？检查 cli.py →
    是 → 更新 user_guide.md「CLI 命令一览」树
    否 → 无需更新

Phase 5:
  验证 guide 中的 revoke/rotate 命令示例与 cli.py 定义一致
```

### 场景 C：用户要求更新 observability design 补充各能力的事件上报格式（反向影响扫描）

```
用户指令: "observability design 补充各能力的事件上报格式详细设计"

Phase 1:
  目标模块: observability
  收集 ground truth:
    - observability 源码中各能力的事件上报实现（字段、格式、触发条件）
    - code_scanner、prompt_scanner、pii_checker 各自产生的事件字段
  更新 design:
    - observability/architecture.md → 新增「各能力事件格式」章节
      （描述 code_scanner 的 event_type/severity、prompt_scanner 的 injection_type、
       pii_checker 的 pii_category 等字段）
  检查 related_designs:
    - observability.related_designs: code_scanner, prompt_scanner, pii_checker, skill_ledger
    - 本次仅描述事件格式，未改变模块间接口 → 跳过 design 联动更新

Phase 2:
  正向追踪（直接更新的 design 的 consumed_by）:
    observability consumed_by → guide/observability.md, guide/hermes_plugin.md, ...
  反向影响扫描（related_designs → 受影响 design → 其 consumed_by）:
    observability.related_designs: code_scanner, prompt_scanner, pii_checker, skill_ledger
    本次变更内容描述了:
      - code_scanner 的 event_type/severity 字段 → code_scanner design 受影响
      - prompt_scanner 的 injection_type 字段 → prompt_scanner design 受影响
      - pii_checker 的 pii_category 字段 → pii_checker design 受影响
      - skill_ledger 未涉及具体字段 → 不命中
    读取受影响 design 的 consumed_by:
      code_scanner → guide/code_scanner.md, guide/hermes_plugin.md, ...
      prompt_scanner → guide/prompt_scanner.md, guide/hermes_plugin.md, ...
      pii_checker → guide/pii_checker.md, guide/hermes_plugin.md, ...
  合并去重: observability.md, hermes_plugin.md, code_scanner.md, prompt_scanner.md,
          pii_checker.md, openclaw_plugin.md, ...

Phase 3:
  逐一检查受影响 guide：
    - guide/observability.md → 确认事件格式说明与代码一致
    - guide/code_scanner.md → 检查是否包含审计事件说明（什么时候产生事件、事件包含哪些字段）
    - guide/prompt_scanner.md → 同上
    - guide/pii_checker.md → 同上
    - 各 agent guide → 检查 observability 能力描述是否需更新

Phase 4:
  未引入新能力/新 CLI/新配置机制 → user_guide.md 无需更新

Phase 5:
  完整性校验: 所有受影响 guide 全部有明确结论
  跨 guide 一致性: code_scanner.md 中的事件字段描述与 observability.md 中的定义语义一致
```

### 场景 D：用户要求所有模块 design 补充错误处理章节

```
用户指令: "所有模块的 design 都补充错误处理章节"

Phase 1:
  目标模块: code_scanner, prompt_scanner, pii_checker, sandbox, skill_ledger, observability
  收集 ground truth:
    - 逐个读取 code_scope 定位源码
    - 提取各模块的异常处理、降级逻辑、超时机制
  更新 design:
    - 每个模块的 architecture.md → 新增「错误处理」章节
  检查 related_designs:
    - 模块间异常传播关系（如 hook wrapper 统一包裹）
    - 若跨模块异常处理有统一模式 → 检查是否需在各 design 中交叉引用

Phase 2:
  汇总所有模块的 consumed_by，合并去重
  结果可能覆盖全部 guide 文件

Phase 3:
  逐一检查所有 guide：
    - 若 guide 中已有「异常行为」描述且与代码一致 → 无需更新
    - 若 guide 中缺失或与代码不一致 → 补充/修正

Phase 4:
  未引入新能力/新 CLI/新配置机制 → user_guide.md 无需更新
  但检查「Fail-Open 设计」段落是否仍精确反映实际行为

Phase 5:
  完整性校验：所有 consumed_by guide 全部有明确结论
  跨 guide 一致性：各 guide 中的 fail-open 描述语义一致
```

## 注意事项

- **不要从 design 文档机械复制**到 guide，guide 需要重新组织为用户视角
- **guide 中的示例命令必须可执行**，不要编造不存在的参数或子命令
- **不可跳过任何 consumed_by 目标**：当 design 变更涉及多个 consumer guide 时，必须逐一检查更新，不可仅更新「最直接相关」的一个
- **组件 design 变更必须传播到所有 agent guide**：例如 code_scanner design 新增配置说明 → code_scanner.md + hermes_plugin.md + openclaw_plugin.md + codex_plugin.md + cosh_extension.md 全部需要检查
- **agent hook design 变更必须反向检查关联 design**：当 agent hook design 新增了组件配置说明（如 code-scan 的 enable_block），对应组件的 design 视为受影响，其 `consumed_by` 全部纳入影响清单（包括组件自身 guide 和其他 agent guide）
- 更新 guide 后检查 frontmatter `sources` 是否需要新增/移除模块引用
- 新增 design 模块时，同步创建 `_meta.yaml`（含 code_scope + related_designs + consumed_by）和 `adr/` 目录
- 文档语言：中文为主，代码/命令/路径保持英文原样
