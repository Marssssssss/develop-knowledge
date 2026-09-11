# 06 DevOps

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-容器化/](./01-容器化/) | Docker、镜像、OCI |
| [02-CI-CD/](./02-CI-CD/) | GitHub Actions / GitLab CI / Jenkins |
| [03-监控与可观测性/](./03-监控与可观测性/) | Prometheus / Grafana / OpenTelemetry |
| [04-Kubernetes/](./04-Kubernetes/) | Pod / Service / Controller / Operator |
| [05-IaC与配置管理/](./05-IaC与配置管理/) | Terraform / Ansible / Pulumi |
| [06-SRE与可靠性工程/](./06-SRE与可靠性工程/) | SLI / SLO / 错误预算 / 复盘文化 |

## 已完成 demo 索引

| demo | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 045-047 | [01-容器化/](./01-容器化/) | Namespace 隔离 / Cgroups v2 / OCI 镜像格式(容器三基石) | C/Python/Go · Python/Go |
| 048-050 | [02-CI-CD/](./02-CI-CD/) | DAG 流水线调度 / 内容寻址缓存 / 蓝绿与金丝雀发布 | C/Python/Go |

## 待研究

- [ ] Dockerfile 最佳实践（多阶段构建）
- [ ] GitHub Actions 工作流
- [ ] OpenTelemetry 三大支柱（Trace/Metric/Log）
- [ ] K8s Operator 模式
- [ ] Terraform 状态管理与 drift(见 05-IaC与配置管理/README)
