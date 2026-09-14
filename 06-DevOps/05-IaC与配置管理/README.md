# IaC 与配置管理

> Infrastructure as Code:用可版本化的代码定义基础设施与配置,替代手工点击控制台。本目录沉淀 Terraform / Ansible / Pulumi 等工具的原理级 demo。

## 领域简介

- **IaC** = 把基础设施(网络、计算、存储)与系统配置(装包、改配置文件)写成**人类可读、可版本控制的声明式文件**,由引擎负责收敛到期望状态。Terraform 官方定义:"an infrastructure as code tool that lets you build, change, and version cloud and on-prem resources safely and efficiently"
- 两大范式:
  - **声明式(Declarative)**:描述"最终态"(Terraform / Ansible playbook 的 desired-state 声明 / CloudFormation);引擎自己算 diff
  - **命令式(Imperative)**:描述"步骤"(Shell 脚本、Ansible task 的顺序执行)
- **状态(State)**:Terraform 用 state 文件记录真实基础设施,官方称其为"source of truth for your environment",plan 阶段即对比配置与 state 的差异
- **幂等性**:Ansible 官方原文:"When the system is in the state your playbook describes, Ansible does not change anything, even if the playbook runs multiple times"

## 两大代表工具对比

| 维度 | Terraform | Ansible |
| --- | --- | --- |
| 定位 | 云资源供给(provisioning) | 配置管理与自动化(configuration) |
| 语言 | HCL(声明式 DSL) | YAML playbook(声明期望 + 顺序 task) |
| 架构 | 本地/CI 执行,经 provider 调云 API | **Agentless**:SSH(现有系统凭据)直连目标机 |
| 状态 | 显式 state 文件(tfstate,建议远程后端) | 无状态(实时查询目标机现状收敛) |
| 核心工作流 | Write → **Plan**(执行计划,人工审批) → Apply | playbook 顺序执行 + handler 通知 |
| 生态 | Registry 上千个 provider(AWS/Azure/GCP/K8s/GitHub…) | 模块库(inventory/roles/modules/vault) |

Terraform 核心工作流(官方):**Write**(定义资源,可跨多云)→ **Plan**(基于现有基础设施与配置生成"将创建/更新/销毁什么"的执行计划)→ **Apply**(按资源依赖图正确顺序执行,无依赖资源并行创建)。

典型分工:**Terraform 建基础设施 → Ansible 配置机器内部**(装软件、打补丁、发应用)。

## 待研究(知识点清单,后续轮次消化)

- [x] Terraform state 深入:远程后端(S3/GCS)、state 锁、drift 检测与 `terraform plan` 差异来源 ✓ demo 107
- [x] HCL 语法与资源依赖图:`depends_on` / 隐式依赖 / `for_each` 与 `count` ✓ demo 102-103
- [x] Terraform provider 插件机制:协议、Registry、OpenTofu(MPL-2.0 分叉,BSL 1.1 事件后) ✓ demo 102-106
- [x] Ansible playbook 执行模型:inventory → play → task → module → handler,幂等实现 ✓ demo 104
- [x] Ansible 变量体系与 Vault(加密敏感变量) ✓ demo 108-109
- [x] Pulumi:通用语言(TS/Python/Go)写 IaC,与 Terraform 引擎模型的异同 ✓ demo 110
- [x] GitOps 流水线:IaC 代码的 PR 审批 → plan 预览 → apply 自动化 ✓ demo 111
- [ ] OpenTofu 与 Terraform 的 state 格式互操作、`removed` 块(1.7+)与 `import` 块(1.5+)声明式迁移
- [ ] Ansible Collections 与 Execution Environment(EE)的依赖打包

## 已完成 demo 索引(2026-09-13 起;2026-09-15 增补 107-111)

### HCL 解析器 (Demo 102)
- C 手写 lexer + 递归下降 parser(208 行)/ Python regex 字典式(281 行)/ Go bufio.Scanner + 递归下降(277 行)
- 实现 attribute/block/字符串字面量/嵌套 object,按 HCL Native Syntax Specification
- 输出:嵌套 dict / pretty-printed tree / JSON

### 资源依赖图与拓扑排序 (Demo 103)
- C 邻接表 + Kahn 分层(254 行)/ Python 邻接表 dict(232 行)/ Go map[string]struct{}(236 行)
- 实现 Terraform 8 步图构建 → Kahn 拓扑分层 → `terraform apply -parallelism=N` 切片
- 4 demo:菱形 / depends_on 显式依赖 / 环检测 / parallelism=10 跨批

### Ansible 幂等模块 (Demo 104)
- Python ansible.builtin 风格(290 行)/ Go 镜像(294 行)/ JS Node.js(229 行)
- 实现 file/package/service 三个核心模块的 desired-state 检查 + `--check` dry-run 模式
- 验证:3 遍执行,第 1 次触发,后两次 0 changed;check_mode 不真改

### 模板引擎 (Demo 105)
- C 极简 lexer + 渲染(195 行)/ Python miniJinja(299 行)/ Go stdlib text/template(106 行)
- 实现 `{{ var }}` 替换 + `{% if %}` + `{% for %}` + `|` 过滤器链
- Go 版直接用 stdlib,体现 Jinja2 → Go 模板的语法对照 (`.Var` vs `var`)

### State diff & Plan (Demo 106)
- C 手写 JSON 子集解析 + diff(299 行)/ Python(268 行)/ Go encoding/json(294 行)
- 解析 Terraform state v4 JSON,字段级 diff desired config vs state,输出 +/~/- 列表
- 4 demo:全新初始化 / drift / 部分删除 / noop

### Terraform 状态锁与远程后端 (Demo 107)
- C(条件写 + `ID=..;Operation=..` 扁平记录解析,300 行)/ Python `ObjectStore.put_if_absent` + `LockInfo`(231 行)/ Go(224 行)
- 核心机制:**条件写(compare-and-swap)** 是 S3(`If-None-Match: *`)与 DynamoDB(`attribute_not_exists(LockID)`)的同一原语;
  state 对象键 = `<key>`(default workspace)或 `env:/<ws>/<key>`(命名 workspace),锁对象 = `<key>.tflock`
- 锁内容含 `ID` 作 **nonce**:解锁/强解锁必须携带同一个 `ID`,否则拒绝(防止 A 的锁被 B 误删)
- `force_unlock` 做 advisory 删除,`is_stale` 按 timeout 判过期;6 组场景(拒绝二次加锁 / 释放 / workspace 隔离 / 错误 nonce / 正确 nonce / 过期清理)

### Ansible 变量优先级 (Demo 108)
- Python(201 行)/ Go(251 行),`PRECEDENCE` 表为 Ansible 官方文档 "Understanding variable precedence" 的 22 级原文字面量
- 核心机制:**后者覆盖前者**,但 inventory 内"更具体的组胜"不靠加载顺序 —— 用小数偏移
  (`INVENTORY_BASE = 3`,`level = 3 + depth*0.1`)把"更具体"编码成同层内的更大 key,故与加载顺序无关
- 覆盖:22 级全序、三层 inventory 嵌套、last-write-wins、role vars、`hash_behavior` 的 dict 合并 vs 替换

### Ansible-Vault 格式 (Demo 109)
- Python `aes.py`(180 行,从零实现 AES-256,含 S-box p/q 递推生成)+ `vault_format.py`(201 行)/ Go(300 行,用 `crypto/aes` + `cipher.NewCTR`)
- 核心机制:**PBKDF2-HMAC-SHA256(10000 轮)→ 80 字节密钥材料**按 32/32/16 切成 `aes_key / hmac_key / iv`;
  密文用 AES-256-CTR(逐块 counter 加密),**HMAC 对密文计算**并前置,校验用 `hmac.compare_digest`(常量时间)
- 1.1 / 1.2 两种 header(`$ANSIBLE_VAULT;1.2;AES256;<vault-id>`),body 是 `hexlify` 后按 **80 列硬换行**的装甲文本
- 明文体先做 **RFC 5652 PKCS#7 padding**(补齐到 16 的整数倍,恰好整块时补一整块)
- 验证:往返 / 篡改一字节被拒 / 错密码被拒 / 同明文 4 次加密得 4 个不同载荷(随机 salt)/ padding 15→16、16→32、17→32;
  AES 单测锚定 **FIPS-197 附录 C.3** 官方测试向量 `8ea2b7ca516745bfeafc49904b496089`

### Pulumi 资源注册与 Preview (Demo 110)
- Python(300 行)/ Go(3 文件:`types.go` 82 + `engine.go` 229 + driver 105)
- 核心机制:三组件分工 —— **language host**(跑用户程序,SDK 在此)+ **deployment engine**(维护期望状态、
  比对并出 plan)+ **resource provider**(真正调云 API);与 Terraform 的本质差异是"程序是**真跑**的"
- **URN** = `urn:pulumi:<stack>::<project>::<parentType$>type::<name>`,复用被删资源的关键是 URN 稳定
- `Output` 的 `unknown` 在 preview 阶段向上传播:`unknown` 参与派生则结果仍是 `unknown`(故 preview 会显示 "(known after apply)")
- 资源按依赖做 **Kahn 分层**;plan diff 四态 `create / update / replace / delete`(replace = `deleteBeforeReplace` 决定先删后建还是先建后删)
- 自动命名:`name` 省略时由引擎按 `<type>-<hex>` 生成,改名会体现为 create+delete 两条

### GitOps 调和与漂移检测 (Demo 111)
- Python(`model.py` 134 行 + `gitops_reconcile.py` 247 行)/ Go(`model.go` 156 行 + 主 276 行)
- 核心机制:**每对 (revision SHA, params) 只成功同步一次**,失败不自动重试(需人工 rollback 或改 spec);
  调和周期默认 120 s,控制器加 **60 s jitter**(避免雪崩),`selfHeal` 超时 5 分钟
- 门控顺序(高优先级先判):`无 pending` → `未开 automated` → `有 live drift 且未开 selfHeal` → `已同步且未开 selfHeal` →
  `该 pair 已失败` → `仅孤儿且 prune 关` → 否则执行 sync
- `live_drift = bool(stale) and not git_moved`:孤儿资源不算 live drift(避免误触发)
- Kubernetes **Server-Side Apply** 的 `managedFields` 记录字段所有权:同一字段被两个控制器写 → 冲突;
  `force=True` 才夺取所有权;空值字段会被释放(交还所有权)
- 9 个场景全部按预期:自动同步 / 手动 spec 不自动 / drift 不自愈 / selfHeal 自愈 / 失败不重试 / prune 关保孤儿 / 开 prune 清理 / SSA 共享所有权 / SSA 冲突与 force

## 参考资料(实际阅读过的权威来源)

- [What is Terraform — HashiCorp Developer 官方文档](https://developer.hashicorp.com/terraform/intro) — IaC 定义、Write/Plan/Apply 三阶段工作流、state 作为 source of truth、资源依赖图并行、providers/Registry(全文阅读)
- [Introduction to Ansible — Ansible Community Documentation 官方文档](https://docs.ansible.com/ansible/latest/getting_started/introduction.html) — agentless(SSH + 现有凭据)、playbook 声明期望状态、幂等性原文、零停机滚动更新(全文阅读)
- [Terraform — Backend Type: s3](https://developer.hashicorp.com/terraform/language/backend/s3) — `use_lockfile` 与 `.tflock` 后缀、DynamoDB `dynamodb_table` 的 `LockID` 属性、`workspace_key_prefix` 默认 `env:` 与 `<workspace_key_prefix>/<workspace>/<key>` 的对象键布局、`force_unlock` 与 stale lock 处置(全文阅读)
- [Ansible — Using Variables: Understanding variable precedence](https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_variables.html) — 22 级优先级完整列表原文、"last definition wins"、"more specific wins" 在 inventory 组层级上的含义、`hash_behaviour` 的合并/替换语义(全文阅读)
- [Ansible Vault — format of encrypted files / vault-id](https://docs.ansible.com/ansible/latest/vault_guide/vault_encrypting_content.html) — `$ANSIBLE_VAULT;1.1;AES256` 头部、PBKDF2 派生密钥材料的切分与长度、HMAC 对密文的校验、payload 的十六进制与换行规则(全文阅读)
- [Pulumi Docs — How Pulumi works / Resource URNs / Preview vs Up](https://www.pulumi.com/docs/iac/concepts/how-pulumi-works/) — language host / deployment engine / resource provider 三组件、程序真实执行、state 快照与差分、`preview` 与 `up` 的一致性保证(全文阅读)
- [OpenGitOps — GitOps Principles v1.0.0](https://opengitops.dev/) — 四条原则(declarative / versioned & immutable / pulled automatically / continuously reconciled)原文
- [Argo CD Docs — Automated Sync Policy 与 Sync Options](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/) — `automated.selfHeal` / `prune` 默认关闭、`allowEmpty`、调和周期 `timeout.reconciliation`(默认 180 s)与 jitter、失败 pair 不重试的语义(全文阅读)
- [Kubernetes Docs — Server-Side Apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/) — `managedFields` 字段管理、共享字段所有权、`force` 夺取与冲突报错、字段释放语义(全文阅读)
