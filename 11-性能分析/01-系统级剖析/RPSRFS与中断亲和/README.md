# 433 RPS / RFS / XPS 与中断亲和再平衡

> RSS 把中断散到多队列，RPS 在**软件层**再做一次分发，RFS 进一步把内核处理挪到**消费该流的应用线程所在 CPU**，XPS 管发送方向。四者都靠同一件事选 CPU：**flow hash 取模**。而切换 CPU 的代价是乱序——RFS 用「旧 CPU 队列已排空」等三条判据守住顺序。

## 1. 简介

本 demo 依据 Linux 内核文档《Scaling in the Linux Networking Stack》实现：

- CPU 位图（`/proc/irq/<IRQ>/smp_affinity`、`rps_cpus`、`xps_cpus`）的解析与往返；
- RPS 的 `get_rps_cpu()` 取模选核；
- RFS 的 `rps_sock_flow_table` / `rps_dev_flow_table` 两表联动与**防乱序三条判据**；
- RPS flow limit 的「最近 256 报文过半即丢」；
- XPS 的 CPU→TX 队列选择与 `ooo_okay` 换队闸门。

## 2. 原理详解

### 2.1 三层分发，各管一段

| 机制 | 做什么 | 内核版本 | 配置点 |
| --- | --- | --- | --- |
| **RSS**（硬件） | NIC 用 Toeplitz hash 索引 indirection table 选接收队列，MSI-X 每队列一个 IRQ | — | `/proc/irq/<IRQ>/smp_affinity` |
| **RPS**（软件） | 中断下半部调 `get_rps_cpu()`，flow hash 对 `rps_cpus` 列表取模，入队目标 CPU backlog 并发 IPI | 2.6.35 | `/sys/class/net/<dev>/queues/rx-<n>/rps_cpus` |
| **RFS** | 把目标改成「正在消费该流的用户态线程所在 CPU」 | 2.6.35 | `rps_sock_flow_entries` + `rps_flow_cnt` |
| **aRFS** | 硬件版 RFS，驱动 `ndo_rx_flow_steer` 直接把流导到应用线程本地队列（需 ntuple 过滤） | 2.6.35 | `ethtool -K ntuple on` |
| **XPS** | 发送方向选 TX 队列，减少队列锁争用与完成时的缓存未命中 | 2.6.38 | `xps_cpus` / `xps_rxqs` |

RPS 相对 RSS 的三个好处：任意 NIC 可用、软件加协议哈希容易、**不提高硬件中断率**（代价是引入 IPI）。

### 2.2 RFS 的两张表与「为什么不能随便换 CPU」

- `rps_sock_flow_table`（全局）：记 **desired CPU**，在 `inet_recvmsg` / `inet_sendmsg` / `tcp_splice_read` 时更新。条目数 `/proc/sys/net/core/rps_sock_flow_entries`，中等负载服务器建议 **65536**，大服务器 **1048576 或更高**，会向上取整到 2 的幂。
- `rps_dev_flow_table`（每队列）：记 **current CPU + 一个 counter**。这个 counter 是该流上次入队时目标 CPU backlog 的 **tail counter（= head counter + 队列长度）**，即「该流最后一个已入队元素」。

`desired != current` 时，只有满足以下**任一**条件才切换：

1. 当前 CPU 的队列 head counter **≥** 表项记录的 tail counter（老 CPU 上该流没有残留）；
2. current 未设置（`>= nr_cpu_ids`）；
3. current 处于 offline 状态。

否则报文继续发往老 CPU——**宁可牺牲局部性也不制造乱序**，这是 RFS 的核心设计约束。

多队列时 `rps_flow_cnt` 通常取 `rps_sock_flow_entries / N`，文档示例：131072 总条目 ÷ 16 队列 = **8192**。

### 2.3 RPS flow limit：CPU 争用时优先保小流

`CONFIG_NET_FLOW_LIMIT` 默认编译进内核但**默认不开启**，按 CPU 位图开启（`/proc/sys/net/core/flow_limit_cpu_bitmap`）：

- 触发线：输入队列长度超过 `netdev_max_backlog` 的 **50%**；
- 统计窗口：最近 **256 个**报文的 per-flow 计数（哈希表默认 **4096** 桶）；
- 丢包规则：某流在新报文到达时占比超过 ratio（**默认一半**）→ 丢它的新包；
- 其它流只有队列达到 `netdev_max_backlog` 才丢——所以大流不会被"掐死"，只是被削峰。

### 2.4 CPU 位图的坑：最低字在前

`smp_affinity` / `rps_cpus` 是逗号分隔的 32 位十六进制字，**最低 32 位写在最前面**。所以 `"00000000,00000001"` 是 CPU 32，不是 CPU 1。手写掩码时这个方向极易搞反。

## 3. 对比

| | 选 CPU 依据 | 是否跨 NUMA | 主要代价 |
| --- | --- | --- | --- |
| RSS | 硬件 Toeplitz hash + indirection table | 取决于 IRQ 亲和 | 需硬件支持 |
| RPS | 软件 flow hash % CPU 列表 | 不感知（配置时自己挑同 memory domain 的 CPU） | IPI + backlog 排队 |
| RFS | 消费线程所在 CPU | **会**把处理拉到应用侧 NUMA 节点 | 两表查找 + 切核判据 |
| aRFS | 同 RFS，硬件执行 | 同 RFS | 需驱动 + ntuple |
| XPS | CPU→TX 队列映射 / RX→TX 映射 | 同 CPU 侧 | 仅在多队列有意义 |

## 4. 环境与运行方式

```bash
cd 11-性能分析/01-系统级剖析/RPSRFS与中断亲和
python rps_rfs_check.py     # 35 条断言，全部实跑通过
go run rps_rfs.go           # 需 Go 工具链（本机无，走人工审查 + 机械核查）
```

## 5. 关键代码

```python
def rfs_decide(desired, current, queue_head, recorded_tail, nr_cpu_ids, offline=()):
    if desired == current:
        return current
    drained = queue_head >= recorded_tail   # 老 CPU 已排空
    unset   = current >= nr_cpu_ids         # current 未设置
    offline = current in offline            # current 已下线
    return desired if (drained or unset or offline) else current
```

## 6. 性能边界

- 每条 RPS 分发都要发一次 **IPI**，中小包场景下 IPI 开销可能吃掉并行收益；文档建议高中断率时把中断 CPU 本身从 `rps_cpus` 里排除。
- RSS indirection table 至少 4 倍于队列数，4x 表会带来约 **16%** 的队列间不均衡（文档认为可接受）。
- flow limit 的桶数（4096）远大于 CPU 数才有细粒度识别大流的能力；`flow_limit_table_len` **只在分配新表时读取**，运行中改不生效。
- `netdev_max_backlog` 文档只给了实验值（1000 / 10000），未给内核默认值——本 demo 用 1000 作默认值并显式标注。

## 7. 注意事项与常见坑

1. **`rps_cpus = 0` 是默认，等于关着**，改完要确认非 0。
2. **irqbalance 会覆盖手动写的 `smp_affinity`**——排查"绑核不生效"先看它。
3. 位图**最低字在前**，别把 `"00000000,00000001"` 当成 CPU 1。
4. RSS 已 1:1 覆盖所有 CPU 时，RPS 通常是**冗余**的，只会多一次 IPI。
5. flow limit 是"削峰"不是"断流"：小流不受影响，大流仍保持连通。
6. XPS 换队列要 `skb->ooo_okay`（TCP 在全部数据被 ACK 后设置），否则会制造发送乱序。
7. `tx_maxrate` 默认 0 = 不限速，单位是 Mbps。

## 8. 参考资料（已读）

- [Linux Kernel — Scaling in the Linux Networking Stack](https://docs.kernel.org/networking/scaling.html)——RPS/RFS/aRFS/XPS 定义与 sysfs/proc 路径、rps_dev_flow 的 CPU+counter 语义、防乱序三条判据、65536/1048576/8192 示例值、flow limit 的 256 报文窗口与 4096 桶、RSS 4x 表 16% 不均衡、2.6.35/2.6.38 引入版本
- 同目录 [中断与软中断剖析/](../中断与软中断剖析/)（demo 106，`smp_affinity` 掩码与 `softnet_stat` 已覆盖，本 demo 补 RPS/RFS 与调优侧）
