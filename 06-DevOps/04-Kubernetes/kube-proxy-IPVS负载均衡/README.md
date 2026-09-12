# kube-proxy IPVS 负载均衡与多调度算法

## 简介

kube-proxy 是 Kubernetes 集群每个节点上运行的网络组件,负责实现 Service 的虚拟 IP(VIP)机制。当客户端访问 Service ClusterIP 时,kube-proxy 把流量转发到后端 Pod。**IPVS 模式**是其中一种高性能实现:它把 Service IP 注册到 Linux 内核的 IP Virtual Server(IPVS)中,由 IPVS 内核模块完成 4 层负载均衡,比 iptables 模式在数万 Service 规模下更快。

**关键概念**:
- **kube-proxy 三种 Linux 模式**:iptables(默认)、ipvs(弃用,1.40 默认禁用/1.43 完全移除)、nftables(1.33+ 稳定,推荐)
- **IPVS 调度算法**:11 种可选(`rr` / `wrr` / `lc` / `wlc` / `lblc` / `lblcr` / `sh` / `dh` / `sed` / `nq` / `mh`),配置项 `ipvs.scheduler`
- **Session Affinity**:基于源 IP 的持久连接(`persistent 10800s` = 180 min)
- **kube-ipvs0 dummy interface**:Service IP 必须绑到这个 dummy interface,内核才会响应 ARP

## 原理详解

### 拓扑(三步走,IPVS proxier 启动时)

```
kubectl apply -f nginx-service.yaml
         │
         ▼
┌──────────────────────────────────────┐
│ 1. 创建 dummy interface: kube-ipvs0 │   ← 所有节点都会做
│    ip link add kube-ipvs0 type dummy │
└──────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────┐
│ 2. 把 Service IP 绑到 kube-ipvs0    │   ← 否则 ARP 不响应
│    ip addr add 10.0.0.1/32 dev kube-ipvs0
└──────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────┐
│ 3. 创建 IPVS virtual server          │
│    ipvsadm -A -t 10.0.0.1:80 -s rr   │
│    ipvsadm -a -t 10.0.0.1:80 -r 10.244.0.1:8080 -m
│    ipvsadm -a -t 10.0.0.1:80 -r 10.244.0.2:8080 -m
└──────────────────────────────────────┘
         │
         ▼
内核 IPVS hash table 接管 4 层负载均衡
```

**NAT 模式(`-m`)** 是唯一支持端口映射的模式(也是 kube-proxy 默认模式),其他模式(DR / IPIP / TUN)不支持端口映射。

### 11 个调度算法(摘自官方文档)

| 算法 | 全称 | 选后端规则 | 适用场景 |
|---|---|---|---|
| `rr` | Round Robin | 轮转 | 默认,均衡 |
| `wrr` | Weighted RR | 按权重轮转 | 后端异构(新旧机器) |
| `lc` | Least Connection | 选活跃连接最少 | 长连接、连接时长不一 |
| `wlc` | Weighted LC | `(C+1)/W` 最小 | 加权 + 长连接 |
| `lblc` | Locality-based LC | 同 src → 同后端,过载则换 | 缓存亲和 |
| `lblcr` | LBLC with Replication | 同 lblc + 复制 | 缓存亲和 + 副本 |
| `sh` | Source Hashing | `hash(src) % n` | 纯会话保持 |
| `dh` | Destination Hashing | `hash(dst) % n` | 后端做缓存(同 dst 同后端) |
| `sed` | Shortest Expected Delay | `min((C+1)/W)` | wlc 简化版 |
| `nq` | Never Queue | 优先空 server,否则 sed | 突发流量 |
| `mh` | Maglev Hashing | Google 一致性哈希(LUT) | 大规模缓存 |

公式说明:
- **wlc/sed**:选 `(active_conn + 1) / weight` 最小的后端
- **lblc**:相同源 IP 的流量优先发往同一后端;过载则切到连接较少的服务器,保留这一决策
- **nq**:任一后端 `active_conn == 0` 直接返回(无等待),全忙时 fallback sed

### Session Affinity(持久连接)

官方 IPVS 博客:
> "When a Service specifies session affinity, the IPVS proxier will set a timeout value (180min=10800s by default) in the IPVS virtual server."

```
ipvsadm -ln
TCP  10.102.128.4:3080  rr  persistent 10800
-> 10.244.0.235:8080    Masq  1  0  0
```

`persistent 10800` 表示同一源 IP 在 180 min 内始终落到同一后端。Session Affinity 与调度算法正交:`sh` / `mh` 是天然的 session affinity,`rr` / `lc` 需 IPVS persistent 标志实现。

### 为什么 IPVS 被弃用

官方原文(K8s 1.36):
> "The ipvs proxy mode is deprecated... the kernel IPVS API turned out to be a bad match for the Kubernetes Services API, and the ipvs backend was never able to implement all of the edge cases of Kubernetes Service functionality correctly."

- **v1.40**:默认禁用(可通过 `--feature-gates=KubeProxyIPVS=true` 重新启用)
- **v1.43**:完全移除
- **替代**:`nftables` 模式(1.33+ 稳定,推荐)或 `iptables` 模式(性能已大幅改进)

## 对比 / 选型

| 维度 | iptables | ipvs | nftables |
|---|---|---|---|
| 数据结构 | 线性链表(O(n) 查找) | 哈希表(O(1) 查找) | 哈希表 |
| 规则同步速度 | 慢(每 Service+endpoint 一条) | 快(哈希表) | 快 |
| 万级 Service 性能 | 瓶颈(数十分钟同步) | 良好 | 良好 |
| 调度算法丰富度 | 仅 random / rr | 11 种 | nftables 自身 + 内置 hash |
| 弃用/推荐 | 1.37 默认 | 已弃用 | 1.33+ stable,推荐 |
| Linux 内核要求 | ≥ 2.6 | IPVS 模块 | ≥ 5.13 |

| 算法 | 何时选 |
|---|---|
| `rr`(默认) | 后端同质、长短连接混合 |
| `lc` / `wlc` | 后端处理时长差异大(异构机器) |
| `sh` / `mh` | 需要 session affinity 且 IPVS persistent 10800s 不够灵活 |
| `dh` | 后端是缓存节点(同 dst 落到同节点) |
| `sed` / `nq` | 突发流量,避免延迟 |

## 环境准备

- **操作系统**:Windows / Linux / macOS(纯算法模拟,不依赖内核)
- **语言版本**:
  - C:任意 C99 编译器
  - Python:3.10+
  - Go:1.18+
- **依赖**:无第三方依赖,仅标准库

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -std=c99 ipvs_lb.c -o ipvs_lb
./ipvs_lb
```

### Python

```bash
python3 ipvs_lb.py
```

### Go

```bash
go run ipvs_lb.go
```

## 关键代码片段

### Python:11 个调度算法核心实现

```python
# sed: (C+1)/U minimize
def lb_sed(svc):
    return min(range(len(svc.backends)),
               key=lambda i: (svc.backends[i].active_conn + 1) / svc.backends[i].weight)

# nq: 优先空 server
def lb_nq(svc):
    for i, b in enumerate(svc.backends):
        if b.active_conn == 0:
            return i
    return lb_sed(svc)

# Maglev Hashing 简化版
class MaglevTable:
    def __init__(self, backends):
        self.size = len(backends) * 137
        self.lookup = [-1] * self.size
        for i, b in enumerate(backends):
            for k in range(137):
                pos = hash_ip(f"{b.ip}/{i}/{k}") % self.size
                while self.lookup[pos] != -1:
                    pos = (pos + 1) % self.size
                self.lookup[pos] = i
```

### C:`-m statistic --mode random` 替代:lb_lc 选最少连接

```c
static int lb_lc(service_t *svc) {
    int best = 0;
    for (int i = 1; i < svc->n_backends; i++) {
        if (svc->bs[i].active_conn < svc->bs[best].active_conn) best = i;
    }
    return best;
}
```

### Go:`hash` 用 FNV-1a 32 bit

```go
func hashIP(s string) uint32 {
    h := uint32(2166136261)
    for _, ch := range []byte(s) {
        h ^= uint32(ch)
        h *= 16777619
    }
    return h
}
```

## 性能与边界

- **rr / lc / sed / nq**:O(1) 或 O(n),n=后端数(通常 ≤ 数百)
- **sh / dh**:O(1)(哈希)
- **mh (Maglev)**:O(1) 选后端 + O(M·N) 内存(M=N×137),M=1000 时 ~140 KB
- **IPVS 整体**:哈希表为底层数据结构,内核空间;比 iptables 链式查找快 10-100×
- **规模**:实测可支撑 5000+ 节点集群,Service 数 < 数万级别
- **弃用原因**:IPVS 内核 API 不能完整覆盖 K8s Service 所有边界(如 SCTP / ExternalName / 部分 headless 行为)

## 注意事项与常见坑

- **kube-ipvs0 dummy interface 必须存在**:不创建的话 Service IP 无法 ARP,VIP "ping 不通"
- **NAT 模式限制**:只有 NAT 模式支持端口映射,DR/IPIP/TUN 不行;若 Service port ≠ Pod port 必须用 NAT
- **`persistent 10800s`**:超过 180 min 后 src 会重新调度(可能落到不同 Pod),客户端需要接受
- **IPVS 弃用**:生产新集群直接选 nftables;旧集群升级时显式 `--proxy-mode=iptables` 或 `nftables`
- **统计模块(`-m statistic --mode random`)**:iptables 模式默认随机选 endpoint,非 rr;需在 iptables 规则里显式 `--mode random`
- **lblc vs sh 区别**:lblc 在过载时切换后端但保留决策(下次同 src 优先用新决策);sh 永远按 hash,绝不切换
- **Maglev vs sh 区别**:Maglev 是**一致性哈希**(后端增减只扰动 1/N 流量),sh 是普通取模(后端变动全扰动)
- **wlc vs sed 区别**:wlc 标准公式 `(C/W)` 但 0 权重的后端永远选不到(分母为 0);sed 用 `(C+1)/W` 避免,实际 kube-proxy IPVS 用 sed 公式

## 参考资料(实际阅读过的权威来源)

- [Virtual IPs and Service Proxies | Kubernetes](https://kubernetes.io/docs/reference/networking/virtual-ips/) — 3 种 Linux 模式(iptables/ipvs/nftables)+ 11 个调度算法完整列表 + ipvs 弃用时间线(v1.40 默认禁用,v1.43 完全移除)
- [IPVS-Based In-Cluster Load Balancing Deep Dive | K8s Blog](https://kubernetes.io/blog/2018/07/09/ipvs-based-in-cluster-load-balancing-deep-dive/) — `kube-ipvs0` 拓扑 + NAT 模式 + Session Affinity 10800s + ipvs vs iptables 性能对比
- [Kubernetes 中文版 Virtual IPs](https://kubernetes.io/zh-cn/docs/reference/networking/virtual-ips/) — 中文术语对照
