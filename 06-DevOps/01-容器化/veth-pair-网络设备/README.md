# veth pair 与 Linux 网桥

> 容器怎么和外部通信?**veth pair** 是"虚拟网线",把宿主与容器的 net namespace 接起来;**bridge** 是软件 L2 交换机,让多个容器互通。

## 简介

容器默认有自己的 network namespace(独立的网络栈、路由表、防火墙规则)。要让容器访问外网,需要"虚拟网线"——**veth pair**。Veth 由 [man4 veth](https://man7.org/linux/man-pages/man4/veth.4.html) 定义:

> "veth devices are always created in interconnected pairs. **Packets transmitted on one device in the pair are immediately received on the other device.**" *(man4 veth 原文)*

经典用法:把 pair 一端在容器 net ns(`veth1`),另一端在宿主(`veth0`)。多个 veth 的宿主端可挂在 bridge 上(`docker0`、`cni0`)形成 L2 互通。**NAT masquerade** 让容器借宿主 IP 出去。

设计参考(读完的真实来源):
- man7 [veth(4)](https://man7.org/linux/man-pages/man4/veth.4.html) — pair 创建与跨 ns 移动
- man7 [network_namespaces(7)](https://man7.org/linux/man-pages/man7/network_namespaces.7.html) — netns 创建
- man7 [ip-link(8) / ip-netns(8)](https://man7.org/linux/man-pages/man8/ip-link.8.html) — 操作工具
- [kernel-internals container-networking](https://kernel-internals.org/net/container-networking/) — Docker veth 与 bridge 在内核的代码(驱动 `drivers/net/veth.c`、bridge `net/bridge/br_forward.c`)

## 原理详解

### veth pair 数据结构

```c
/* drivers/net/veth.c */
struct veth_priv {
    struct net_device __rcu *peer;  // 指向另一端
    atomic64_t dropped;
    struct veth_rq *rq;             // 多 RX queue
};

/* TX → 收方 RX:直接调用 peer 的 RX queue,无硬件 */
static netdev_tx_t veth_xmit(struct sk_buff *skb, struct net_device *dev) {
    rcv = rcu_dereference(priv->peer);
    veth_forward_skb(rcv, skb, ...);
}
```

**关键观察**(kernel-internals):
- veth 实现完全软件(无硬件,`netif_rx()` 直送 peer)
- 两端任一 down,另一端也 down
- `ethtool -S vethA` 可查 `peer_ifindex`(peer 网卡的 ifindex)

### 创建与跨 netns

```bash
# 1. 创建一对,均在 host netns
ip link add veth0 type veth peer name veth1

# 2. 把 veth1 移到容器 netns
ip link set veth1 netns /proc/<pid>/ns/net
# 或:ip link set veth1 netns <ns-name>    # 由 ip netns add 创建

# 3. 配置两边
ip addr add 10.0.0.1/24 dev veth0           # host 端
ip -n netns_name addr add 10.0.0.2/24 dev veth1
ip -n netns_name link set veth1 up
ip -n netns_name route add default via 10.0.0.1
```

### Linux bridge(L2 交换机)

```bash
# 创建软件 bridge
ip link add docker0 type bridge
ip addr add 172.17.0.1/16 dev docker0
ip link set docker0 up

# 多个 veth 的 host 端成为 bridge port
ip link set veth0 master docker0
ip link set veth2 master docker0

# bridge 学 MAC,转发表 = fdb (forwarding database)
bridge fdb show
```

**bridge 内核代码路径**(`net/bridge/br_forward.c`):
- 帧进入 bridge port → 查 FDB(MAC→port)
- 命中:unicast → 直发该 port
- 未命中:flood(广播到所有 port)
- 同时支持 STP、netfilter hooks、VLAN filtering

### Docker / Kubernetes 真实拓扑

```
┌──────────── host netns ─────────────┐  ┌─── container netns ───┐
│  eth0 (物理, 10.0.0.5/24)           │  │ eth0 (veth1 的 peer)   │
│  docker0 (bridge, 172.17.0.1/16)    │  │ 172.17.0.2/16          │
│   ├── vethA (port)─┐                │  │ ↑                     │
│   └── vethB (port)─┘                │  │                       │
│  iptables MASQUERADE 172.17/16       │  │ ip route default via   │
│                                      │  │   172.17.0.1 dev eth0  │
└──────────────────────────────────────┘  └───────────────────────┘
```

### NAT / masquerade

```bash
# Docker 安装时的 iptables 规则(NAT 表 POSTROUTING)
iptables -t nat -A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE
# nftables 等价:
nft add rule ip nat POSTROUTING ip saddr 172.17.0.0/16 oifname != "docker0" masquerade
```

包从容器 `172.17.0.2` 出去,到 host 出网前源 IP 改为 host eth0 IP;回程反向修改 dest IP 回容器。

## 对比 / 选型

| 接入 | 隔离 | 性能 | 复杂度 | 典型 |
| --- | --- | --- | --- | --- |
| **bridge + veth pair** | 中(L2 命名空间) | 高 | 低 | Docker 默认 |
| **macvlan** | 中(共享 L2) | 高 | 低 | Singularity/HPC |
| **ipvlan** | 中 | 高 | 低 | L3 共享 IP |
| **host networking** | 无(netns=host) | 极 | 极低 | 高性能服务 |
| **none** | 无 | - | 极低 | 离线 demo |
| **Overlay(VXLAN/Geneve)** | 高(跨主机) | 中 | 高 | K8s flannel/calico |

## 环境准备

- Linux 3.0+(veth);2.6.x 已实装
- 工具:`iproute2`(`ip`, `bridge` 子命令)
- C 演示需 root + 链接 libnl-3.0;demo 也给出 rtnetlink 直调的简化版
- Python: subprocess 调 `ip` 命令,或 `pyroute2` 库
- Go: 标准库 net + `syscall.Syscall` rtnetlink

## 运行方式

### Python(无 root,运行 shell 命令字符串打印)
```bash
python3 veth_demo.py topology           # 打印 Docker/CNI 典型拓扑示意
python3 veth_demo.py commands           # 列出创建 pair/bridge 的命令模板
python3 veth_demo.py inspect            # 解析 /sys/class/net 现状
```

### C(rtnetlink 直调创建 veth pair,需 root)
```bash
gcc -O2 -Wall -I. veth_demo.c -o veth_demo && sudo ./veth_demo create veth0 veth1
sudo ./veth_demo list                  # 列现有 link 标识 veth 类型
```

### Go(syscall 直调)
```bash
cd go && go run veth_demo.go dump      # 打印命令序列
```

## 关键代码片段

### C 版 rtnetlink RTM_NEWLINK(`veth_demo.c`)

```c
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
/* IFLA_INFO_KIND = "veth" */
struct rtattr *info = NLMSG_TAIL(nlh);
NLA_PUT_STRING(info, IFLA_INFO_KIND, "veth");
/* IFLA_INFO_DATA 下嵌入 VETH_INFO_PEER(嵌套另一 IFLA) */
```

### Python 版 inspect(`/sys/class/net`)

```python
import os, glob
for path in glob.glob("/sys/class/net/*"):
    if os.path.exists(path + "/tun_flags"): continue
    ifindex = open(path + "/ifindex").read().strip()
    print(f"  {os.path.basename(path):<10}  ifindex={ifindex}")
```

## 性能与边界

- veth pair 转发 ~0.5-1μs(纯软件,kernel-2-skb-memcpy)
- bridge forwarding ~1-2μs(FDB 查 + skb 复制)
- 对比物理 10G NIC 5-10μs,veth 仅慢 5-10x
- veth MTU 沿用底层(默认 1500),可在两端不同但要较小
- `IFLA_NET_NS_FD` 通过 fd 直接加入 netns(无需 `/proc/<pid>/ns/net`)
- 同一 pair 可在两端设置不同 IP(MAC 必然不同,内核随机)
- 5.0+ `cgroup-veth`(BPF cgroup hook)可限流:veth XDP/eBPF 加速 26Mpps 单核

## 注意事项与常见坑

1. **veth pair 任一 down,另一也 down**——忘了 up 容器端会假"网络断"
2. **`bridge-master` 设置后,该 link 不能有 IP 地址**(L3 由 bridge 自己处理)
3. **bridge 上挂 STP 不会自动开启**;`bridge stp on docker0` 才防环路
4. **MASQUERADE 规则丢**:容器不能 ping 通外网,先 `iptables -t nat -L POSTROUTING` 检查
5. **跨主机 veth**:单 host 内 veth 不能跨主机;K8s 用 flannel vxlan/calico vxlan,**内核 GRE/VXLAN** 是另一套机制
6. **CNI 工具替代手写**:K8s 用 CNI 插件(flannel/calico/cilium);学习时手写命令更直观
7. **rm/netns 关联**:`ip netns delete <name>` 后,该 ns 内 veth 会消失;且容器内仍持有 fd 仍可见

## 参考资料(实际阅读过的权威来源)

- [man7 veth(4)](https://man7.org/linux/man-pages/man4/veth.4.html) — pair 语义、跨 ns 移动、ethtool peer_ifindex(全文)
- [man7 network_namespaces(7)](https://man7.org/linux/man-pages/man7/network_namespaces.7.html) — netns 创建、地址、路由、隔离范围
- [man7 ip-link(8)](https://man7.org/linux/man-pages/man8/ip-link.8.html) — `ip link add type veth peer name`、`ip link set netns`、`vlan`、`master`
- [man7 brctl(8)](https://man7.org/linux/man-pages/man8/bridge.8.html) — Linux bridge 用户态工具;新 `bridge` 命令(替代 brctl)
- [kernel-internals container-networking](https://kernel-internals.org/net/container-networking/) — Docker/K8s 完整桥接 + `veth_xmit` / `br_forward` 内核代码
- [man7 iptables(8) / nft(8)](https://man7.org/linux/man-pages/man8/iptables.8.html) — MASQUERADE 与 NAT 表的 POSTROUTING 钩子
