#!/usr/bin/env python3
"""TCP 连接建立的两条队列：半连接（SYN）队列 与 全连接（accept）队列。

用离散事件状态机复现 man7 listen(2) + 内核 ip-sysctl 文档描述的语义：

  * Linux 2.2 起，listen(fd, backlog) 的 backlog 限制的是【全连接队列】
    （已完成三次握手、等 accept() 的 ESTABLISHED 连接），不是半连接队列；
  * 全连接队列上限 = min(backlog, net.core.somaxconn)（静默取小）；
  * 半连接队列上限 = net.ipv4.tcp_max_syn_backlog（每监听器）；
  * 全连接队列满时，net.ipv4.tcp_abort_on_overflow=0（默认）→ 静默丢弃
    客户端的最终 ACK，让客户端重传（自愈）；=1 → 直接回 RST；
  * 半连接队列满时，tcp_syncookies=1（默认）→ 改用 SYN Cookie，不占队列。

运行：python3 main.py      （自带断言自检，失败即非零退出）
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 内核文档：一个 SYN_RECV 请求套接字约 304 字节
BYTES_PER_SYN_RECV = 304
# 内核文档：tcp_max_syn_backlog 在低内存机器上的最小值
SYN_BACKLOG_FLOOR = 128
# 内核文档：somaxconn 默认值（Linux 5.4 起；更早是 128）
SOMAXCONN_MODERN = 4096
# man7 sendfile 无关；这里是 tcp_synack_retries 的默认 5 次重传
SYNACK_RETRIES = 5


@dataclass
class Stats:
    syn_received: int = 0
    syn_dropped: int = 0
    syncookie_issued: int = 0
    syncookie_completed: int = 0
    established: int = 0            # 三次握手完成，准备进全连接队列
    accept_queue_overflow: int = 0  # 全连接队列满（最终 ACK 被忽略）
    rst_sent: int = 0               # tcp_abort_on_overflow=1 时发出的 RST
    accepted_by_app: int = 0
    recovered_after_overflow: int = 0  # 曾被忽略、靠重传最终成功的连接数（自愈）
    gave_up: int = 0                # 重传次数耗尽仍未成功
    max_syn_q: int = 0
    max_accept_q: int = 0


@dataclass
class Listener:
    """一个 LISTEN 套接字的两条队列 + 相关 sysctl 配置。"""

    backlog: int                              # listen(fd, backlog) 的实参
    somaxconn: int = SOMAXCONN_MODERN         # net.core.somaxconn
    tcp_max_syn_backlog: int = 1024           # net.ipv4.tcp_max_syn_backlog
    syncookies: bool = True                   # net.ipv4.tcp_syncookies = 1
    abort_on_overflow: bool = False           # net.ipv4.tcp_abort_on_overflow = 0
    syn_q: list[int] = field(default_factory=list)    # 半连接队列（存 client id）
    accept_q: list[int] = field(default_factory=list)  # 全连接队列（存 client id）
    stats: Stats = field(default_factory=Stats)
    ignored: set[int] = field(default_factory=set)     # 最终 ACK 曾被忽略的 client

    @property
    def accept_limit(self) -> int:
        """内核实际生效的全连接队列上限：backlog 与 somaxconn 取小。"""
        return min(self.backlog, self.somaxconn)

    @property
    def syn_limit(self) -> int:
        """半连接队列上限（每监听器）。"""
        return self.tcp_max_syn_backlog

    # ---- 三次握手三个阶段 ----
    def on_syn(self, client: int) -> str:
        """收到 SYN。返回内核采取的动作。"""
        self.stats.syn_received += 1
        if len(self.syn_q) < self.syn_limit:
            self.syn_q.append(client)
            self.stats.max_syn_q = max(self.stats.max_syn_q, len(self.syn_q))
            return "synack_sent"                 # 进半连接队列，回 SYN+ACK
        if self.syncookies:
            # 半连接队列溢出 + syncookies 开启 → 不占队列，把状态编码进 seq
            self.stats.syncookie_issued += 1
            return "syncookie_sent"
        self.stats.syn_dropped += 1
        return "syn_dropped"                     # 客户端只能重传 SYN

    def on_final_ack(self, client: int) -> str:
        """收到第三次握手的 ACK：尝试把连接从半连接队列搬到全连接队列。"""
        if client in self.syn_q:
            self.syn_q.remove(client)
        if len(self.accept_q) < self.accept_limit:
            self.accept_q.append(client)
            self.stats.established += 1
            self.stats.max_accept_q = max(self.stats.max_accept_q, len(self.accept_q))
            if self.stats.syncookie_issued:
                self.stats.syncookie_completed += 1
            if client in self.ignored:          # 之前被忽略过 → 这次靠重传补上了
                self.ignored.discard(client)
                self.stats.recovered_after_overflow += 1
            return "moved_to_accept_queue"
        # 全连接队列满：由 tcp_abort_on_overflow 决定丢弃还是 RST
        self.stats.accept_queue_overflow += 1
        if self.abort_on_overflow:
            self.stats.rst_sent += 1
            return "rst"                         # 客户端看到 connection refused
        self.ignored.add(client)
        return "ack_ignored"                     # 静默丢弃 → 客户端重传（自愈）

    def accept(self) -> int | None:
        """应用调用 accept()：从全连接队列取一个连接。"""
        if not self.accept_q:
            return None
        c = self.accept_q.pop(0)
        self.stats.accepted_by_app += 1
        return c


# ------------------------------------------------------------------ 事件模拟
@dataclass
class Event:
    tick: int
    kind: str          # "syn" | "ack"
    client: int
    attempt: int = 0


def retry_delay(base: int, attempt: int) -> int:
    """真实 TCP 的重传是**指数退避**的（tcp_syn_retries / tcp_synack_retries：
    约 1s、2s、4s、8s、16s、32s）。固定间隔重试是常见建模错误——它会让
    "自愈"几乎不可能发生，从而把"应用太慢"误判成"协议不自愈"。"""
    return base * (2 ** attempt)


def simulate(n_clients: int, accept_every: int, ticks: int = 400,
             syn_retx: int = 2, rtt_ticks: int = 4,
             clients_per_tick: int = 1, **kwargs) -> Listener:
    """离散事件模拟：客户端 SYN 到达后，隔 rtt_ticks 个 tick 才回来 ACK。

    accept_every    ：应用每多少个 tick 调用一次 accept()（模拟应用消费速度）。
    syn_retx        ：重传退避基数，第 k 次重传在 base * 2^k 个 tick 之后。
    rtt_ticks       ：一个 RTT 相当于多少个 tick。SYN 洪泛的关键就在于
                      **到达速率远高于 RTT 内的处理速率**，队列才会堆积。
    clients_per_tick：每个 tick 到达多少个 SYN（>1 即模拟洪泛/突发）。
    """
    ln = Listener(**kwargs)
    events: list[Event] = []
    for i in range(n_clients):
        events.append(Event(tick=i // clients_per_tick, kind="syn", client=i))
    next_accept = accept_every

    for tick in range(ticks):
        # 1) 处理本 tick 的所有事件（同一 tick 内按到达顺序）
        for ev in [e for e in events if e.tick == tick]:
            events.remove(ev)
            if ev.kind == "syn":
                action = ln.on_syn(ev.client)
                if action in ("synack_sent", "syncookie_sent"):
                    events.append(Event(tick=tick + rtt_ticks, kind="ack",
                                        client=ev.client))
                elif ev.attempt >= SYNACK_RETRIES:
                    ln.stats.gave_up += 1
                else:
                    events.append(Event(tick=tick + retry_delay(syn_retx, ev.attempt),
                                        kind="syn", client=ev.client,
                                        attempt=ev.attempt + 1))
            else:  # 最终 ACK
                action = ln.on_final_ack(ev.client)
                if action == "ack_ignored":
                    # 服务端会重传 SYN+ACK，客户端重新 ACK
                    if ev.attempt >= SYNACK_RETRIES:
                        ln.stats.gave_up += 1
                    else:
                        events.append(Event(tick=tick + retry_delay(syn_retx, ev.attempt),
                                            kind="ack", client=ev.client,
                                            attempt=ev.attempt + 1))
                elif action == "rst":
                    pass  # 客户端立刻失败，不再重试

        # 2) 应用消费
        if tick >= next_accept:
            ln.accept()
            next_accept = tick + accept_every

        # 没人排队了才收工；注意 accept_q 里可能还有积压要排空
        if not events and not ln.accept_q:
            break
    return ln


# ------------------------------------------------------------------ 自检
