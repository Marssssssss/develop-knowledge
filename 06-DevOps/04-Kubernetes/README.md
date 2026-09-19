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
| 407 | [Scheduler调度框架/](./Scheduler调度框架/) | Scheduling Framework 12 扩展点 / Filter node 内短路 / PostFilter 抢占只提名 / NormalizeScore / Reserve 逆序 Unreserve / Bind 短路 | C / Python / Go |
| 408 | [HPA水平扩缩容/](./HPA水平扩缩容/) | ceil 公式 + tolerance 0.1 / 缺失指标缩容按 100%、扩容按 0% / 方向反转不动 / scaleUpLimit=max(2x,4) / 稳定窗口扩收取 min、缩容取 max | C / Python / Go |
| 409 | [NetworkPolicy网络策略/](./NetworkPolicy网络策略/) | 隔离按方向独立 / 策略叠加是并集 / from OR × ports AND / ns+pod 同元素为 AND / ipBlock except / policyTypes 缺省推断 | C / Python / Go |
| 410 | [ConfigMap与Secret投影更新/](./ConfigMap与Secret投影更新/) | AtomicWriter 12 步 / ..data 符号链接 rename 原子切换 / validatePath 五禁令 / subPath 与 env 不更新 / 延迟 = sync + cache 传播 | C / Python / Go |
| 411 | [HelmChart模板渲染/](./HelmChart模板渲染/) | values 优先级 / null 删键 / 管道值作最后参数 / InstallOrder 36 项 / 未知 Kind 排最后 | C / Python / Go |

## 待研究

- [ ] CRD + Operator + Reconciler(实战级,非模拟)
- [ ] VPA / KEDA(自动扩缩)
- [ ] Service Mesh(Istio Sidecar 注入,Linkerd)
- [ ] Pod 拓扑分布约束(TopologySpreadConstraints)
- [ ] CSI 存储编排(provision / attach / mount)
- [ ] 准入控制(Admission Webhook / ValidatingAdmissionPolicy)
- [ ] kubelet Pod 准入与驱逐(eviction thresholds)

> 历史欠账(源文件 >300 行,待专门欠账轮):`Controller-Reconciler模式/c/reconciler.c` 369、
> `Pod生命周期与重启策略/c/pod_lifecycle.c` 349、`Pod生命周期与重启策略/go/pod_lifecycle.go` 320、
> `Controller-Reconciler模式/go/reconciler.go` 315、`kube-proxy-IPVS负载均衡/c/ipvs_lb.c` 311;
> README >200 行:`Controller-Reconciler模式` 247、`kube-proxy-IPVS负载均衡` 217。
