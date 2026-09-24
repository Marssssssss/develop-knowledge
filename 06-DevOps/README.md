# 06 DevOps

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-容器化/](./01-容器化/) | Docker、镜像、OCI（含 CNI 网络控制面 / OCI 分发协议 / runc 创建流程 / 切根与传播 / cgroup 线程模式，ID 690-694） |
| [02-CI-CD/](./02-CI-CD/) | GitHub Actions / GitLab CI / Jenkins |
| [03-监控与可观测性/](./03-监控与可观测性/) | Prometheus / Grafana / OpenTelemetry |
| [04-Kubernetes/](./04-Kubernetes/) | Pod / Service / Controller / Operator |
| [05-IaC与配置管理/](./05-IaC与配置管理/) | Terraform / Ansible / Pulumi |
| [06-SRE与可靠性工程/](./06-SRE与可靠性工程/) | SLI / SLO / 错误预算 / 燃烧率告警 / 过载保护（首批 5 demo，ID 517-521） |
| [07-DevSecOps/](./07-DevSecOps/) | 安全左移：SAST / SCA / SBOM / 密钥管理 / IaC 扫描（首批 5 demo，ID 571-575） |
| [08-平台工程/](./08-平台工程/) | 内部开发者平台：软件目录 / 权限框架 / 黄金路径模板 / Crossplane / KubeVela OAM（首批 5 demo，ID 626-630） |

## 已完成 demo 索引

| demo | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 045-047 | [01-容器化/](./01-容器化/) | Namespace 隔离 / Cgroups v2 / OCI 镜像格式(容器三基石) | C/Python/Go · Python/Go |
| 048-050 | [02-CI-CD/](./02-CI-CD/) | DAG 流水线调度 / 内容寻址缓存 / 蓝绿与金丝雀发布 | C/Python/Go |
| 051-053 | [03-监控与可观测性/](./03-监控与可观测性/) | Prometheus 指标模型 / W3C Trace Context / histogram_quantile | C/Python/Go |
| 347-351 | [03-监控与可观测性/](./03-监控与可观测性/) | PromQL 范围向量与外推 / TSDB 存储与 Head 块 / OpenTelemetry Collector 管线 / Alertmanager 分组抑制静默 / Loki 日志存储与 LogQL（可观测性主干第二批，补齐查询·存储·采集·路由·日志） | C/Python/Go |
| 054-056 | [04-Kubernetes/](./04-Kubernetes/) | Pod 生命周期+重启策略 / kube-proxy IPVS 调度 / Controller Reconciler | C/Python/Go |
| 407-411 | [04-Kubernetes/](./04-Kubernetes/) | kube-scheduler 调度框架扩展点 / HPA 期望副本数与四层阻尼 / NetworkPolicy 语义求值 / ConfigMap-Secret 投影原子写入 / Helm 模板渲染与 InstallOrder（Kubernetes 主干第二批） | C/Python/Go |
| 102-106 | [05-IaC与配置管理/](./05-IaC与配置管理/) | HCL 解析器 / Terraform 资源依赖图+Kahn 拓扑 / Ansible 幂等模块 / Jinja2 模板引擎 / Terraform State diff & Plan (IaC 主干五件套) | C/Python/Go / Python+JS |
| 107-111 | [05-IaC与配置管理/](./05-IaC与配置管理/) | Terraform 远程后端与 state 锁(条件写+nonce) / Ansible 变量优先级 22 级 / Ansible-Vault 格式(AES-256-CTR+HMAC) / Pulumi 资源注册与 Preview / GitOps 调和与漂移检测(SSA 字段所有权) | C/Python/Go |
| 292-296 | [02-CI-CD/](./02-CI-CD/) | GitHub Actions 触发与表达式语义 / GitLab CI rules 与父子流水线 / Jenkins 声明式 Pipeline 时序 / Argo CD 同步与漂移检测 / 制品晋升与语义化版本（CI-CD 主干第二批） | Python/Go |
| 462-466 | [05-IaC与配置管理/](./05-IaC与配置管理/) | Terraform 重构块与状态搬迁(moved/removed/import) / lifecycle 元参数与依赖图变换 / 插件协议发现与版本选择 / Ansible 集合 FQCN 解析与 runtime 元数据 / 计划期 unknown 值传播（IaC 主干第四批） | Python/Go |
| 517-521 | [06-SRE与可靠性工程/](./06-SRE与可靠性工程/) | SLI 窗口与聚合口径(Prometheus 外推+OpenSLO rolling/calendar) / 错误预算与燃烧率(Sloth 因子+三种 budgetingMethod) / 多窗口多燃烧率告警(短窗叫停 55 分钟) / 退避与抖动(AWS 模拟器转写) / 过载保护与自适应并发(Envoy 梯度控制器稳态闭式)（SRE 主干首批） | Python/Go |
| 626-630 | [08-平台工程/](./08-平台工程/) | Backstage 软件目录实体模型与关系推导 / 权限框架与条件策略(ALLOW·DENY·CONDITIONAL + criteria) / Scaffolder 软件模板与黄金路径(`${{ }}` 保类型) / Crossplane XRD 与 Composition 组合选择 / KubeVela OAM 应用模型与定义修订（平台工程主干首批） | Python/Go |
| 571-575 | [07-DevSecOps/](./07-DevSecOps/) | Semgrep 通用匹配(`...`/`<...>`/metavar 绑定) / PURL 规范化与 CycloneDX VEX+SPDX 转译 / DSSE PAE 与 P-256(RFC6979) 制品签名 / detect-secrets 与 gitleaks 熵口径对比 / OPA Rego 编译期 safety 重排与 Unify(官方 31 条向量对拍)（DevSecOps 主干首批） | Python/Go |

| 152-156 | [01-容器化/](./01-容器化/) | OverlayFS 联合挂载 / Capabilities 权限分割 / Seccomp-BPF 过滤 / veth pair 网络 / OCI Runtime Spec | C/Python/Go |
| 237-241 | [01-容器化/](./01-容器化/) | Docker 层缓存失效(缓存键链+COPY 校验和) / 多阶段构建与镜像瘦身 / user namespace UID 映射 / rootless 容器 / cgroup v2 + eBPF 附加与 BPF token | Python/Go / Python/Go/C |
| 690-694 | [01-容器化/](./01-容器化/) | CNI 插件模型与 host-local IPAM 轮询(链式 prevResult+Range 规范化) / OCI 分发协议(14 端点+分块上传 416+标签分页+referrers) / runc 双进程同步协议(9 常量+seccomp 两个插入点+exec fifo) / pivot_root 六条限制与 shared subtree 传播 / cgroup v2 线程模式与 cpuset 分区(local·remote·三条件)（容器化第四批） | Python/Go |

## 待研究

- [x] Dockerfile 最佳实践（多阶段构建）→ 已建于 `01-容器化/多阶段构建/`
- [ ] 镜像签名与供应链（cosign / SLSA provenance）
- [x] GitHub Actions 工作流 → demo 292（`02-CI-CD/GitHubActions工作流语义/`）
- [ ] OpenTelemetry 三大支柱（Trace/Metric/Log）
- [ ] K8s Operator 实战模式(见 04-Kubernetes/README)
- [x] Terraform 状态管理与 drift(见 05-IaC与配置管理/README) ✓ demo 102-111
- [x] 内部开发者平台(IDP)：软件目录 / 黄金路径 / 组合式资源 / 应用抽象 → 已建于 `08-平台工程/`（ID 626-630）
- [ ] Backstage TechDocs 与插件后端 wiring
- [ ] 平台工程度量（DORA / SPACE / DevEx）与 Team Topologies
- [ ] Crossplane composition functions 协议与 KubeVela CUE 求值
- [x] 容器网络 CNI 插件模型（IPAM / 链式插件）→ 已建于 `01-容器化/CNI插件模型与IPAM/`（ID 690）
- [x] OCI 分发协议（registry 端点 / 分块上传 / 标签分页）→ 已建于 `01-容器化/OCI分发协议与镜像拉取/`（ID 691）
- [ ] 镜像签名与供应链在分发层的落地（referrers + cosign）
- [ ] 容器运行时监控（cadvisor / containerd metrics）与运行时类 RuntimeClass
