# Kubernetes

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [Pod生命周期与重启策略/](./Pod生命周期与重启策略/) | Pod phase 5 态 + restartPolicy 3 值 + 指数退避 + Sidecar 独立性 |
| [kube-proxy-IPVS负载均衡/](./kube-proxy-IPVS负载均衡/) | IPVS 模式 + 11 个调度算法 + Session Affinity |
| [Controller-Reconciler模式/](./Controller-Reconciler模式/) | Informer 缓存 + Workqueue + Reconcile + 409 Conflict |

## 已完成 demo 索引

| demo | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 054 | [Pod生命周期与重启策略/](./Pod生命周期与重启策略/) | Pod 5 态 phase / restartPolicy Always·OnFailure·Never / 指数退避 10s→300s / Sidecar 独立性 | C / Python / Go |
| 055 | [kube-proxy-IPVS负载均衡/](./kube-proxy-IPVS负载均衡/) | kube-proxy iptables vs ipvs vs nftables / IPVS 11 调度算法 / kube-ipvs0 dummy / Session Affinity 10800s | C / Python / Go |
| 056 | [Controller-Reconciler模式/](./Controller-Reconciler模式/) | r.Get/List 走本地 cache / r.Update 直连 apiserver / workqueue 去重 / 409 Conflict 重试 | C / Python / Go |

## 待研究

- [ ] Scheduler 调度框架(predicate + priority + binding)
- [ ] CRD + Operator + Reconciler(实战级,非模拟)
- [ ] Helm Chart 模板渲染(text/template)
- [ ] HPA / VPA / KEDA(自动扩缩)
- [ ] NetworkPolicy(L3/L4 网络策略)
- [ ] ConfigMap / Secret 热更新(reloader 模式)
- [ ] Service Mesh(Istio Sidecar 注入,Linkerd)
