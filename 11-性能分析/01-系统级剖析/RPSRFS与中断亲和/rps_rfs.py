"""RPS / RFS / XPS / flow limit / IRQ 亲和 —— 模型层。

权威来源（本 demo 逐条回读）：Linux 内核文档
"Scaling in the Linux Networking Stack"
https://docs.kernel.org/networking/scaling.html

文档确立的事实：
1. RPS 是 RSS 的**软件实现**：在接收中断下半部（netif_rx / netif_receive_skb）
   调 get_rps_cpu()，用 flow hash 对 rps_cpus 列表取模选 CPU，入队该 CPU 的
   backlog，并发 IPI 唤醒。rps_cpus = 0（默认）即禁用，报文留在中断 CPU 上处理。
2. RFS 把内核处理挪到**消费该流的应用线程所在 CPU**：
   - rps_sock_flow_table 是全局表，记 desired CPU，在 inet_recvmsg /
     inet_sendmsg / tcp_splice_read 时更新；条目数由
     /proc/sys/net/core/rps_sock_flow_entries 控制（中等负载建议 65536，
     大机 1048576+，向上取整到 2 的幂）。
   - rps_dev_flow_table 是每队列的表（rps_flow_cnt），每项记 current CPU +
     一个 counter（= 该流上次入队时目标 CPU backlog 的 tail = head + qlen）。
   - 切 CPU 的三条判据：desired != current 且（旧 CPU 队列 head >= 记录的
     tail counter，即老 CPU 上没有该流的残留）**或** current 未设置
     （>= nr_cpu_ids）**或** current 已 offline。这是为了避免乱序。
   - 多队列时 rps_flow_cnt 通常 = rps_sock_flow_entries / N（131072/16 = 8192）。
3. XPS 选发送队列：xps_cpus（CPU→TX 队列）或 xps_rxqs（RX 队列→TX 队列）；
   命中多个队列时用 flow hash 再选一个；选定后缓存在 socket 上，只有
   skb->ooo_okay 置位（TCP 在全部数据被确认后设置）才允许改队列。
4. RPS flow limit（CONFIG_NET_FLOW_LIMIT 默认编译但**默认不开启**）：
   入队长度超过 netdev_max_backlog 的一半时开始统计**最近 256 个报文**的
   per-flow 计数，某流占比超过 ratio（默认一半）就丢它的新包；其它流要等到
   队列达到 netdev_max_backlog 才丢。哈希表默认 4096 桶。
5. IRQ 亲和：/proc/irq/<IRQ>/smp_affinity，默认任意 CPU，irqbalance 会覆盖
   手动设置；Accelerated RFS 用的 CPU→硬件队列映射就是这张表的反向映射。
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Sequence

# ---------------------------------------------------------------- CPU 位图 ----


def parse_cpu_mask(text: str) -> List[int]:
    """解析与 smp_affinity / rps_cpus 相同的位图格式。

    格式是逗号分隔的 32 位十六进制字，**最低字在前**。例如
    "0000000f" -> [0,1,2,3]；"00000000,00000001" -> [32]。
    """
    text = text.strip()
    if not text or text == "0":
        return []
    words = text.split(",")
    cpus: List[int] = []
    for i, w in enumerate(words):
        val = int(w, 16)
        for bit in range(32):
            if val >> bit & 1:
                cpus.append(i * 32 + bit)
    return cpus


def format_cpu_mask(cpus: Sequence[int]) -> str:
    """把 CPU 列表还原成位图文本（逆运算，便于断言往返）。"""
    if not cpus:
        return "0"
    nwords = (max(cpus) // 32) + 1
    words = [0] * nwords
    for c in cpus:
        words[c // 32] |= 1 << (c % 32)
    return ",".join(f"{w:08x}" for w in words)


def rps_flow_cnt_per_queue(sock_flow_entries: int, nr_queues: int) -> int:
    """多队列设备每队列的 rps_flow_cnt = 总条目 / 队列数（文档示例 131072/16）。"""
    if nr_queues <= 0:
        raise ValueError("nr_queues 必须为正")
    return sock_flow_entries // nr_queues


def roundup_pow2(n: int) -> int:
    """rps_sock_flow_entries 与 rps_flow_cnt 都会向上取整到最近的 2 的幂。"""
    if n <= 0:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


# ------------------------------------------------------------------- RPS ------


def rps_select_cpu(flow_hash: int, rps_cpus: Sequence[int], irq_cpu: int) -> int:
    """RPS 选 CPU：flow hash 对 CPU 列表长度取模；列表为空则留在中断 CPU。"""
    if not rps_cpus:
        return irq_cpu  # rps_cpus = 0（默认）⇒ RPS 禁用
    return rps_cpus[flow_hash % len(rps_cpus)]


# ------------------------------------------------------------------- RFS ------


def rfs_decide(
    desired_cpu: int,
    current_cpu: int,
    queue_head: int,
    recorded_tail: int,
    nr_cpu_ids: int,
    offline_cpus: Sequence[int] = (),
) -> int:
    """RFS 是否把 current CPU 更新为 desired CPU（防乱序的三条判据）。

    返回本报文实际应该入队的 CPU。
    """
    if desired_cpu == current_cpu:
        return current_cpu
    drained = queue_head >= recorded_tail          # 老 CPU 上该流的残留已排空
    unset = current_cpu >= nr_cpu_ids              # current 未设置
    offline = current_cpu in offline_cpus          # current 已下线
    if drained or unset or offline:
        return desired_cpu
    return current_cpu                             # 保守：留在老 CPU 以免乱序


# ------------------------------------------------------------ RPS flow limit --


class FlowLimit:
    """RPS flow limit：用最近 256 个报文的 per-flow 占比决定要不要丢新包。"""

    HISTORY = 256

    def __init__(self, netdev_max_backlog: int = 1000, ratio: float = 0.5,
                 table_len: int = 4096):
        self.max_backlog = netdev_max_backlog
        self.ratio = ratio
        self.table_len = table_len
        self.history: deque = deque(maxlen=self.HISTORY)
        self.dropped = {None: 0}

    def threshold(self) -> float:
        """文档：输入队列超过 netdev_max_backlog 的一半时开始统计。"""
        return self.max_backlog * 0.5

    def active(self, backlog_len: int) -> bool:
        return backlog_len > self.threshold()

    def should_drop(self, flow_id: int, backlog_len: int) -> bool:
        hist = list(self.history)
        if not self.active(backlog_len) or len(hist) < self.HISTORY:
            self.history.append(flow_id)
            return False
        share = hist.count(flow_id) / len(hist)
        drop = share > self.ratio
        if not drop:
            self.history.append(flow_id)
        return drop


# ------------------------------------------------------------------- XPS ------


def xps_select_queue(
    cpu_queue_map: Dict[int, List[int]],
    cpu: int,
    flow_hash: Optional[int] = None,
) -> Optional[int]:
    """XPS：CPU → 候选队列；命中多个时用 flow hash 再选一个。"""
    cands = cpu_queue_map.get(cpu)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    if flow_hash is None:
        return cands[0]
    return cands[flow_hash % len(cands)]


def xps_may_change_queue(ooo_okay: bool) -> bool:
    """只有 skb->ooo_okay（TCP 在全部数据被确认后置位）才允许改发送队列。"""
    return ooo_okay


def tx_maxrate_enabled(tx_maxrate: int) -> bool:
    """tx_maxrate 默认 0 表示不限速（单位 Mbps）。"""
    return tx_maxrate > 0
