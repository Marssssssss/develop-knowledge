# eBPF / XDP 包过滤

## 简介

**XDP** (eXpress Data Path) 是 Linux 4.8+(2016 年合并)的**网络包高速处理框架**,让 eBPF 程序在网卡驱动收到包的**最早 hook 点**(skb 分配之前)运行,可按规则丢弃、放行、重定向、回送包;**单核可达 26Mpps 量级**的丢包/转发吞吐,是 Cloudflare、Facebook、Cilium 等大规模 L3/L4 防护系统的基石。

- **关键问题**:传统 Linux 内核网络栈处理一个包需要 skb 分配 → 协议解析 → 路由查找 → netfilter → socket queue → context switch。**对 DDoS 流量,这部分开销完全浪费**(马上要丢弃)。攻击者 1Mpps 就能拖死普通 Linux 服务器。
- **关键概念**:
  - **eBPF**:一种在内核虚拟沙盒运行的字节码(类似 Java VM),由 verifier 静态校验安全性(无越界/无死循环)后才允许在内核加载。最初为 cBPF (1992 BSD 包过滤)扩展,eBPF 是 Linux 3.18+(2014) 全面重写。
  - **XDP** (eXpress Data Path):eBPF 程序在**网卡驱动的 RX 中断处理后立刻执行**(NAPI poll 上下文),查看包数据(MAC/IP/TCP/UDP 头),根据 action code 决定包命运。
  - **5 种 Action**:`XDP_ABORTED`(触发异常 tracepoint)/ `XDP_DROP`(丢,**最常用**)/ `XDP_PASS`(上送内核协议栈)/ `XDP_TX`(同口回送,反射攻击防御)/ `XDP_REDIRECT`(重定向到另一 NIC / CPU / AF_XDP socket,**零拷贝**)
  - **3 种 XDP 模式**:
    - **Native XDP**(default):驱动原生支持,最快
    - **Offloaded XDP**:程序整个 offload 到网卡硬件(Netronome/Intel)
    - **Generic XDP**:不支持原生 XDP 的驱动 fallback,内核协议栈早期调 eBPF,性能最差
- **历史**:Linux 4.8 (2016) 合并由 Høiland-Jørgensen 等人写的 XDP。AF_XDP (Linux 4.18, 2018) 提供 kernel-bypass 的用户态零拷贝 socket,Cloudflare/Cilium 大量采用。Microsoft 在 2022 年移植 XDP 到 Windows。

## 原理详解

### Packet 数据流路径对比

```
                    ┌─────────────────────────────────┐
                    │   Network NIC 收到一个 packet  │
                    └─────────────┬───────────────────┘
                                  ↓
                ┌─────────────────────────────────────────────────────┐
   传统内核栈:   │  硬中断 → skb 分配 → 协议解析 → 路由 → nf_hook    │
                │  → 内核 socket queue → 用户态 recv() context switch │
                └─────────────────────────────────────────────────────┘
                                  ↓
                              ↑↑↑ 慢 (2-5µs/pkt)↑↑↑

                ┌─────────────────────────────────────────────────────┐
   XDP Native:   │  硬中断 → NAPI poll → XDP eBPF 程序(几十 ns)→     │
                │    ├─XDP_DROP: 直接回收 DMA buffer return          │
                │    ├─XDP_TX: 同口反射                              │
                │    ├─XDP_PASS: 进入内核协议栈(只过 XDP,后续骤停) │
                │    └─XDP_REDIRECT: 另 NIC/AF_XDP socket            │
                └─────────────────────────────────────────────────────┘
                                  ↓
                              ↑↑↑ 快 (60ns/pkt 丢包 / 200ns/pkt 转发)↑↑↑
```

### eBPF 程序结构(Linux)

```c
// 必须放 ELF section "xdp" (libxdp 推荐 "xdp" 或 "xdp_prog")
SEC("xdp")
int xdp_filter(struct xdp_md *ctx) {
    void *data     = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;

    // 1) 解析以太网头(必须做 bounds check,否则 verifier 拒绝)
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;  // 包太短直接放行

    // 2) 解析 IPv4 头(按 EtherType 分支)
    if (eth->h_proto != htons(ETH_P_IP))
        return XDP_PASS;

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return XDP_PASS;

    // 3) 业务规则:例如丢弃 src_ip 命中黑名单的包
    __u32 src_ip = ip->saddr;
    __u32 *blacklist = bpf_map_lookup_elem(&blacklist_map, &src_ip);
    if (blacklist)
        return XDP_DROP;   // 黑名单 → 直接丢,内核都看不见

    // 4) 默认放行
    return XDP_PASS;
}

char _license[] SEC("license") = "GPL";   // 必填,GPL 才有 call 五 helper functions 权限
```

### BPF Map (内核态 ↔ 用户态通信)

```c
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __type(key, __u32);          // src_ip (network byte order / 主机序都行)
    __type(value, __u64);        // 命中计数 / 来源标签
    __uint(max_entries, 65536);
} blacklist_map SEC(".maps");
```

用户态通过 libbpf/libxdp 加载 BPF 程序 + 读写 Map。例如:

```bash
# Cloudflare L4Drop 用 XDP 丢 SYN flood 攻击,典型代码:
# 读 ACL → 写入 blacklist_map → attach lo NIC
bpftool map update pinned /sys/fs/bpf/l4drop/blacklist key 0xc0a80101 0 0 0  value 1
```

### XDP Action 详解

| Action | 值 | 用途 | 性能影响 |
| --- | --- | --- | --- |
| `XDP_ABORTED` | 0 | 触发 `xdp_exception` tracepoint,debug 用 | 贵(产生 tracepoint + drop) |
| `XDP_DROP` | 1 | **最常用**:直接丢弃,回收 DMA buffer | 最快 (~60 ns/pkt) |
| `XDP_PASS` | 2 | 上送内核协议栈,后续正常处理 | 触发完整 skb 分配,慢 |
| `XDP_TX` | 3 | 同 NIC 反射回去(SSDP/ICMP reflection defense) | 快 (~150 ns) |
| `XDP_REDIRECT` | 4 | 重定向到另一 NIC / 其它 CPU (cpumap) / AF_XDP socket | 快 (~200 ns),零拷贝 |

### 真实案例

| 项目 | 厂商 | 用法 |
| --- | --- | --- |
| L4Drop | Cloudflare | XDP 早期丢 DDoS 流量,在到达内核前 drop |
| Katran | Facebook | L4 负载均衡,基于 XDP + BPF_MAP_TYPE_DEVMAP |
| Cilium | Isovalent | eBPF 替代 kube-proxy,service mesh 也基于 XDP |
| Unimog | Cloudflare | 边缘 L3/L4 负载均衡,跨 POP 流量调度 |
| Suricata / Snort | 经典 IDS | 也在用 XDP 加速签名匹配 |

## 环境准备

- 操作系统:**Linux kernel 4.18+**(5.10+ 推荐,BPF Type Format 完全稳定)
- 工具链:
  - `clang` / `llvm`(编译 BPF 程序为目标文件)
  - `libbpf` / `libxdp`(用户态 loader)
  - `linux-headers-<kernel-version>`:`vmlinux.h`(用户态 bpf_helpers 头文件,生成:`bpftool btf dump file /sys/kernel/btf/vmlinux format c`)
  - **可选**:`bpftool`(调试/查看挂载的 BPF 程序 + Map)
- **真机或 VM**:要 attach 到一块 NIC 需要 root(本 demo 只**模拟** verifier 视角的程序骨架 + Map 设计,不真加载)
- 语言:**C (BPF) + Python (用户态控制器) + Go (替代 libbpf loader)**

## 运行方式

```bash
# C (BPF 目标文件 + 用户态 loader,无法在内核加载 — 一段静态编译)
#   需要 libbpf-dev:  apt-get install libbpf-dev
clang -target bpf -Wall -Wextra -O2 -c c/xdp_filter.bpf.c -o xdp_filter.bpf.o
gcc -O2 c/xdp_user_loader.c -o xdp_loader -lbpf -lxdp

# Python (用户态控制器,模拟)
python3 python/xdp_filter.py

# Go (用户态 loader 模拟)
cd go && go run xdp_filter.go
```

输出:
1. 打印 BPF 程序的 `SEC("xdp")` 区段与 action code 含义
2. 构造一个 IPv4 TCP/IP 头 → 在 Python 中**模拟** XDP 程序逐步执行(packet boundary check → parse → check map → return action)
3. 用户态"伪 Map"显示黑名单命中情况与统计

## 关键代码片段

### BPF 程序骨架 (`c/xdp_filter.bpf.c`)

```c
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <bpf/bpf_helpers.h>

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __type(key, __u32);          /* src_ip */
    __type(value, __u64);
    __uint(max_entries, 65536);
} blacklist_map SEC(".maps");

SEC("xdp")
int xdp_drop_blacklist(struct xdp_md *ctx) {
    void *data     = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;

    if (eth->h_proto != bpf_htons(ETH_P_IP))
        return XDP_PASS;

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return XDP_PASS;

    __u32 src = ip->saddr;
    __u64 *count = bpf_map_lookup_elem(&blacklist_map, &src);
    if (count) {
        __sync_fetch_and_add(count, 1);
        return XDP_DROP;
    }
    return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
```

### 用户态 Controller (`python/xdp_filter.py`)

```python
from dataclasses import dataclass
@dataclass
class BlacklistEntry:
    src_ip: str
    hits: int = 0

class XDPController:
    """模拟 XDP 程序对每个 IPv4 包的动作决策(教学用,不再跑 BPF 字节码)。"""
    def __init__(self):
        self.blacklist: dict[str, BlacklistEntry] = {}
        self.stats = {'pass': 0, 'drop': 0, 'abort': 0, 'tx': 0, 'redirect': 0}

    def attach_to(self, iface: str):
        # 真实场景: bpf_set_link_xdp_fd(iface, prog_fd, XDP_FLAGS_SKB_MODE)
        print(f"  → attached to {iface} (Native XDP, in real env)")

    def add_block(self, src_ip: str):
        self.blacklist[src_ip] = BlacklistEntry(src_ip)

    def process_packet(self, ipv4_src: str) -> str:
        entry = self.blacklist.get(ipv4_src)
        if entry:
            entry.hits += 1
            self.stats['drop'] += 1
            return 'XDP_DROP'
        self.stats['pass'] += 1
        return 'XDP_PASS'

ctrl = XDPController()
ctrl.add_block('203.0.113.66')   # 加黑名单
ctrl.attach_to('eth0')
print(ctrl.process_packet('198.51.100.7'))   # 'XDP_PASS'
print(ctrl.process_packet('203.0.113.66'))   # 'XDP_DROP'
print(f"stats: {ctrl.stats}")
```

## 性能与边界

- **单核丢包吞吐**(Intel Xeon,10G NIC):
  - XDP Drop ~26 Mpps(60 ns/pkt)
  - XDP TX ~14 Mpps
  - XDP Redirect ~10 Mpps
- 对比传统 iptables DROP:~3-5 Mpps(1000 ns/pkt,慢 17-100 倍)
- **30 Mpps XDP Drop** 在工程上约等于 14 字节一个 packet 的 32 Gbps 流量(以最小 IPv4 包计)
- **CPU 0% 单核 XDP Drop** 即达到 26 Mpps;真正吃 CPU 的是 XDP_PASS(触发完整协议栈,2-5µs/pkt)
- **并发**:XDP 程序本身无锁(Map 多个核并发访问需在 eBPF 侧用 `BPF_MAP_TYPE_PERCPU_*` 类型)+ RX 队列多核 polling(RSS 散列)
- **限制**:不能在 XDP 中调用绝大多数 BPF helper(只有白名单 ~30 个:lookup/update/redirect/...);不能在 XDP 中 sleep;不能分配大内存(只能在栈上 < 512 B);不能调用任何用户态库

## 注意事项与常见坑

- **verifier 拒绝**:任何"循环次数 verifier 推不出上界"、"包 pointer 与 data_end 没验证"、"调用未授权 helper" 都会被拒绝。bpf 程序的每次 ptr 都必须 `if (ptr + 1 > data_end) return XDP_PASS` 防止越界,这是 verifier 强制要求的。
- **Native vs Generic XDP**:Generic XDP 在不支持原生 XDP 的 NIC 上也能跑,但要等内核先把 skb 准备好 + RTNL 锁 → 性能差。不要混淆 `xdp` 与 `xdp_generic`。
- **绑定/卸载 XDP**:程序 attach 到 NIC → `ip link set dev eth0 xdpdrv off` 卸载(需要 root)。
- **`bpf_redirect_map` 配套 `BPF_MAP_TYPE_DEVMAP/XSKMAP/CPUMAP`**:redirect action 必须先用 helper 选目标。
- **Multi-buffer / MTU > 1500**:驱动对 jumbo frame 可能不直接给 XDP 全部 buffer,可能只看到 first buffer;XDP packet 不能跨页面。
- **XDP_PASS 的回环(loop)**:XDP 程序内部把包 redirect 到自己 → 无限循环。要在 redirect 之前打特定 mark 让上游 XDP 实例不再处理。
- **Tail call**(链式 XDP):可以 stack 多个 XDP 程序(`bpf_tail_call()`),实现洋葱式过滤层,合计 16 层。
- **Map 更新 atomic**:`BPF_ANY` 不阻塞,`BPF_NOEXIST` 失败返回;更新时注意 race,生产用 `BPF_MAP_TYPE_LRU_HASH` 比 HASH 更稳。
- **`btf` 与 `vmlinux.h`**:新内核要 `vmlinux.h` 才能在内核态 eBPF 用 CO-RE 重定位,避免每次不同内核要重新编译的麻烦。
- **生产 attach 前必须做 `bpftool prog dump xlated`** 验证 BPF 指令符合预期;有 bug 时 verifier 不会告诉你语义错。

## 对比 / 选型

| 技术 | 位置 | 性能 | 用例 |
| --- | --- | --- | --- |
| **XDP Native** | NIC 驱动 RX 最早 hook | **26 Mpps Drop** | DDoS 防护、LB、精准 QoS |
| **XDP Generic** | 内核协议栈早期 | 中等(5 Mpps) | 测试、旧 NIC 上开发 |
| **XDP Offload** | 网卡硬件 | 几乎无限吞吐 | 运营商级包过滤 |
| **tc eBPF classifier** | 软件(qdisc 队列) | 中等(10 Mpps) | egress 处理、QoS shaping |
| **iptables DROP** | netfilter hook | ~3 Mpps | 简单规则、状态匹配 |
| **nftables** | netfilter | ~3-5 Mpps | 复杂表/集/set 元素 |
| **DPDK** | 用户态(独占网络) | < 100 Mpps 单核 | 极致吞吐,但需专用 NIC + 占用全部 CPU |

## 参考资料(实际阅读过的权威来源)

- [eBPF Docs — Program type BPF_PROG_TYPE_XDP](http://docs.ebpf.io/linux/program-type/BPF_PROG_TYPE_XDP) — XDP context 结构、Action 语义、helper 函数、`xdp_md` 字段(`data`/`data_end`/`data_meta`/`ingress_ifindex`/`rx_queue_index`)
- [Red Hat RHEL 10 — Chapter 3: Getting started with XDP and eBPF](https://docs.redhat.com/de/documentation/red_hat_enterprise_linux/10/html/configuring_firewalls_and_packet_filters/getting-started-with-xdp-and-ebpf) — Native/Generic/Offloaded 三种模式、套接字类型(`AF_XDP`)、各 hook 点权限
- [NXP — What is XDP](https://docs.nxp.com/bundle/REALTIMEEDGEUG/page/topics/rtn/xdp-netc-overview.html) — DMA buffer 零拷贝机制、AF_XDP 4 个 ring (FILL/RX/TX/COMPLETION)、TSN 低延迟应用
- [科普中国 — Linux 网络新基石: XDP 技术是什么?](https://www.kepuchina.cn/article/articleinfo?business_type=100&ar_id=486235) — 5 个 BPF 子系统(XDP driver hook/eBPF 虚拟机/BPF Maps/eBPF verifier/XDP Action)、XDP 框架全景图
- [HandWiki — Express Data Path](https://handwiki.org/wiki/Software:Express_Data_Path) — XDP 起源(Linux 4.8 合并)、Microsoft XDP for Windows(2022-05)、Cloudflare/Facebook 工业应用列表
- [Cilium — XDP Documentation](https://docs.cilium.io/en/stable/network/servicemesh/xdp/) — 实际生产 XDP 使用示例,kube-proxy replacement with XDP
- [Cloudflare Blog — L4Drop: XDP DDoS Mitigations](https://blog.cloudflare.com/l4drop-xdp-conntrack-and-rolling-ddos-deployments/) — Cloudflare L4Drop 生产部署 XDP 抵御 SYN flood 的实战
- [Linux Kernel source — Documentation/networking/filter.txt](https://docs.kernel.org/networking/filter.html) — 内核 BPF/XDP 文档
