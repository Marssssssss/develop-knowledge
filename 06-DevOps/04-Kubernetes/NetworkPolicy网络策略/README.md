# NetworkPolicy 语义求值:隔离、并集与 and/or 陷阱

## 简介

NetworkPolicy 是**声明式、单向、可叠加**的 L3/L4 规则。它的语义有一堆反直觉的地方,而且全部藏在官方文档的散文里:

- **没有策略 ≠ 拒绝,而是全通**。隔离必须靠「有策略选中这个 Pod」显式建立。
- **隔离按方向独立**:入站隔离和出站隔离是两件事,`policyTypes` 决定这次声明覆盖哪个方向。
- **策略之间不冲突,是并集**(additive),所以**永远不会出现「某条策略拒绝」**,只有「没有任何策略允许」。
- **同一条 rule 里 `from` 数组是 OR、条目内是 AND、而 `from × ports` 又是 AND** —— 三层逻辑混在一个缩进结构里。
- 同一个 `from` 元素里写 `namespaceSelector` + `podSelector` 是 **AND**;把它们拆成两个 `-` 元素就变成 **OR**。YAML 缩进改变语义,这是最经典的坑。

本项目把这些写成求值器,并用**官方示例里的那条 NetworkPolicy** 做回归。

## 原理详解

### 隔离:先判定「有没有被隔离」,再看规则

> 原文:*"By default, a pod is non-isolated for egress; all outbound connections are allowed. A pod is isolated for egress if there is any NetworkPolicy that both selects the pod and has 'Egress' in its policyTypes."*

```
是否允许 = 该方向未被隔离
         OR 存在某条 applicable policy 的某条 rule 允许这次连接
```

`applicable` 的判定是 **选中(podSelector 匹配且同 namespace)+ `policyTypes` 含该方向** 两个条件同时成立。

一个直接推论:`podSelector: {}` 的 default-deny 会**把 namespace 内所有 Pod 都隔离**,而不是「只拒绝没被别的策略提到的 Pod」。本项目专门有一条断言:**`podSelector: {}` 的 deny-all 生效后,`db → fe` 也被拒**(因为 fe 同样被隔离),而 `podSelector: {role: db}` 的 deny 则不影响 `db → fe`。

### 叠加是并集,与顺序无关

> 原文:*"Network policies do not conflict; they are additive... Thus, order of evaluation does not affect the policy result."*

两条策略分别放行 6379 与 5432,结果两个端口都通。本项目用**颠倒顺序的同一个集群**回归这一点。

### 三层逻辑:OR / OR / AND

> 原文:*"Each rule allows traffic which matches **both** the `from` and `ports` sections."*

```
rule.allows = (peer1 OR peer2 OR ... ) AND (port1 OR port2 OR ...)
                    ↑ from 数组内 OR              ↑ ports 数组内 OR
policy.allows = rule1.allows OR rule2.allows OR ...     ← rule 之间还是 OR
```

所以官方示例里:三个来源(ipBlock / namespaceSelector / podSelector)**任意之一**都可以访问 6379;但**没有任何来源**可以访问 6380 —— 因为 `ports` 只写了 6379。本项目两条断言分别验证「`role=backend` 不在 from 里 → 拒」与「`role=frontend` 在 from 里但端口是 6380 → 拒」。

### and/or 陷阱:缩进即语义

```yaml
# A:同一个 from 元素 → AND
ingress:
- from:
  - namespaceSelector: {matchLabels: {user: alice}}
    podSelector:       {matchLabels: {role: client}}
```

> 原文:*"This policy contains a single from element allowing connections from Pods with the label role=client in namespaces with the label user=alice."*

```yaml
# B:两个 from 元素 → OR
ingress:
- from:
  - namespaceSelector: {matchLabels: {user: alice}}
  - podSelector:       {matchLabels: {role: client}}
```

> 原文:*"It contains two elements in the from array, and allows connections from Pods in the local Namespace with the label role=client, *or* from any Pod in any namespace with the label user=alice."*

还有一个容易漏的点:**只写 `podSelector` 时它只在 NetworkPolicy 自己的 namespace 内生效**;而写了 `namespaceSelector` 之后,`podSelector` 的作用域变成「被 nsSelector 选中的那些 namespace」(可以是任意 namespace)。本项目第一轮实现把这一点写成了「podSelector 永远限制在 policy 自己的 namespace」,断言立刻把它顶了出来。

### ipBlock 与 except

`except` 从 CIDR 里挖洞。官方示例 `172.17.0.0/16 except 172.17.1.0/24` 实际允许 `172.17.0.0–172.17.0.255` 与 `172.17.2.0–172.17.255.255`。

### policyTypes 的缺省推断

> 原文:*"If no policyTypes are specified on a NetworkPolicy then by default Ingress will always be set and Egress will be set if the NetworkPolicy has any egress rules."*

也就是说**只写了 `ingress:` 的策略不会隔离出站**,反之写了 `egress:` 的策略会**同时**把入站也隔离掉(因为 Ingress 总是被设置,而此时 `ingress` 规则为空 = 全拒)。这是「加了 egress 白名单之后服务突然不通了」的根因。

### 双向都要允许

> 原文:*"For a connection from a source pod to a destination pod to be allowed, both the egress policy on the source pod and the ingress policy on the destination pod need to allow the connection."*

另外两条常被忘的规则:**来自 Pod 所在 node 的入站总是被允许**;**回包被隐式允许**(由 CNI 的连接跟踪实现,不在策略求值范围内)。

## 对比

| 写法 | 语义 |
| --- | --- |
| `podSelector: {}` | 选中该 namespace 内**全部** Pod |
| `policyTypes: [Ingress]` | 只隔离入站,出站不受影响 |
| 不写 `policyTypes` 且无 egress 规则 | 等价于 `[Ingress]` |
| 不写 `policyTypes` 且有 egress 规则 | 等价于 `[Ingress, Egress]` ← 入站也被隔离 |
| `ingress: - {}` | 允许全部来源与端口 |
| 同一 from 元素内 ns+pod selector | AND |
| 拆成两个 from 元素 | OR |
| 多条 NetworkPolicy | 并集(additive),无优先级、无顺序 |
| ipBlock + except | CIDR 挖洞 |

## 环境

- Python 3.8+(仅标准库)
- Go 1.18+(仅标准库)
- C99 编译器(实现体拆到 `networkpolicy_eval_impl.h`,用 `#include` 引入)

## 运行方式

```bash
python python/networkpolicy_eval.py        # 39 项断言
go run go/networkpolicy_eval.go
gcc -std=c99 -o /tmp/np c/networkpolicy_eval.c && /tmp/np
```

## 关键代码

```python
def matches(self, src, src_ns, src_ip):
    if self.ip_block is not None:
        return ip_in_block(src_ip, self.ip_block, self.ip_except)
    if self.ns_selector is not None:
        # 同一元素内 ns × pod 是 AND;此时 podSelector 作用于被选中的 namespace
        if not labels_match(self.ns_selector, src_ns.labels):
            return False
        return labels_match(self.pod_selector, src.labels)
    if self.pod_selector is not None:
        # 只写 podSelector 时,它只在 NetworkPolicy 自己的 namespace 内生效
        if src.ns != self._policy_ns:
            return False
        return labels_match(self.pod_selector, src.labels)
    return True

def allows(self, src, src_ns, src_ip, protocol, port):
    peer_ok = any(p.matches(...) for p in self.peers) if self.peers else True
    port_ok = any(p.matches(protocol, port) for p in self.ports) if self.ports else True
    return peer_ok and port_ok          # peer × port 是 AND
```

## 性能边界

- 单次判定的复杂度是 O(适用策略数 × 规则数 × (peer 数 + 端口数)),`namespaceSelector` 需要额外查 namespace 标签。
- 真实 CNI(Calico / Cilium)把策略编译成 iptables/ipset/eBPF,**不是逐包跑这份求值**;本项目是语义参考实现,用于把规则想清楚。
- 「叠加是并集」意味着策略数量线性放大匹配成本,且**没有任何短路**:没有「deny 优先」的快速拒绝路径。
- NetworkPolicy 只覆盖 L3/L4(TCP / UDP / SCTP),其他协议的行为按网络插件而异(文档明确说明)。
- 规模上限参考:单个 namespace 内策略过多会显著放大 CNI 的规则编译时间,这也是社区倾向用「少量宽策略 + 命名空间划分」而非「大量窄策略」的原因。

## 注意事项与常见坑

- **创建 NetworkPolicy 但网络插件不支持 → 完全没效果**,而且没有任何报错。
- **default-deny egress 会连 DNS 一起挡掉**,必须额外放行集群 DNS —— 文档在 Caution 里专门提醒。
- **同一个 from 元素的 AND / 两个元素的 OR** 只差一层缩进,出错时 `kubectl describe` 看不出来。
- **加了 `egress` 规则会让入站也被隔离**(`policyTypes` 缺省推断)。
- **Pod 不能阻断访问自己**;**来自 Pod 所在节点的流量对入站总是允许**;这两条是隐式规则,策略里看不到。
- **Pod IP 是临时的**,`ipBlock` 应该只用来描述集群外部 IP;描述 Pod 之间关系要用 selector。
- 源/目的 IP 可能被 SNAT/DNAT 改写,在改写前还是改写后应用策略**未定义**,不同插件行为不同。
- 构造测试用例时要注意「对齐陷阱」:如果给 NetworkPolicy 所在的 namespace 自己打上了 `namespaceSelector` 要找的标签,那么「某个 Pod 应被拒绝」的断言会静默失效。本项目踩过一次,已注释说明。

## 参考资料

已实际阅读:

1. Kubernetes 官方文档 — Network Policies,https://kubernetes.io/docs/concepts/services-networking/network-policies/
2. Kubernetes 官方文档 — Declare Network Policy(示例策略),https://kubernetes.io/docs/tasks/administer-cluster/declare-network-policy/
