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

- [ ] Terraform state 深入:远程后端(S3/GCS)、state 锁、drift 检测与 `terraform plan` 差异来源
- [x] HCL 语法与资源依赖图:`depends_on` / 隐式依赖 / `for_each` 与 `count`
- [x] Terraform provider 插件机制:协议、Registry、OpenTofu(MPL-2.0 分叉,BSL 1.1 事件后)
- [x] Ansible playbook 执行模型:inventory → play → task → module → handler,幂等实现
- [ ] Ansible 变量体系与 Vault(加密敏感变量)
- [ ] Pulumi:通用语言(TS/Python/Go)写 IaC,与 Terraform 引擎模型的异同
- [ ] GitOps 流水线:IaC 代码的 PR 审批 → plan 预览 → apply 自动化

## 已完成 demo 索引(2026-09-13)

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

## 参考资料(实际阅读过的权威来源)

- [What is Terraform — HashiCorp Developer 官方文档](https://developer.hashicorp.com/terraform/intro) — IaC 定义、Write/Plan/Apply 三阶段工作流、state 作为 source of truth、资源依赖图并行、providers/Registry(全文阅读)
- [Introduction to Ansible — Ansible Community Documentation 官方文档](https://docs.ansible.com/ansible/latest/getting_started/introduction.html) — agentless(SSH + 现有凭据)、playbook 声明期望状态、幂等性原文、零停机滚动更新(全文阅读)
