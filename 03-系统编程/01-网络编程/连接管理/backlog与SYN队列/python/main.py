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
def self_check() -> int:
    ok = 0

    # 1. 有效上限 = min(backlog, somaxconn)（man7 listen(2) 的"静默取小"）
    l1 = Listener(backlog=511, somaxconn=128)
    assert l1.accept_limit == 128, "somaxconn 更小时应取 somaxconn"
    l2 = Listener(backlog=8, somaxconn=4096)
    assert l2.accept_limit == 8, "backlog 更小时应取 backlog"
    assert Listener(backlog=65535, somaxconn=4096).accept_limit == 4096
    ok += 1

    # 2. 默认常量与内核文档一致
    assert SOMAXCONN_MODERN == 4096, "Linux 5.4 起 somaxconn 默认 4096"
    assert SYN_BACKLOG_FLOOR == 128, "tcp_max_syn_backlog 低内存最小值 128"
    ok += 1

    # 3. 正常场景：应用及时 accept → 两条队列都不溢出，全部建连
    ln = simulate(n_clients=40, accept_every=2, ticks=4000, backlog=64,
                  somaxconn=4096)
    assert ln.stats.syn_dropped == 0 and ln.stats.accept_queue_overflow == 0, \
        "应用及时消费时不应溢出"
    assert ln.stats.accepted_by_app == 40, f"应全部被 accept，实际 {ln.stats.accepted_by_app}"
    ok += 1

    # 4. 应用太慢 → 全连接队列溢出；默认 abort_on_overflow=0 不发 RST，
    #    被忽略的连接靠"服务端重传 SYN+ACK、客户端重发 ACK"补上（自愈）
    slow = simulate(n_clients=60, accept_every=20, ticks=4000, backlog=4,
                    somaxconn=4096, abort_on_overflow=False)
    assert slow.stats.accept_queue_overflow > 0, "应用太慢应触发全连接队列溢出"
    assert slow.stats.rst_sent == 0, "默认策略不应发 RST"
    assert slow.stats.recovered_after_overflow > 0, \
        "ack_ignored 应可自愈（重传后成功），recovered 却为 0"
    ok += 1

    # 5. abort_on_overflow=1 → 每次溢出都直接 RST：连接快速失败，但**失去了自愈机会**
    hard = simulate(n_clients=60, accept_every=20, ticks=4000, backlog=4,
                    somaxconn=4096, abort_on_overflow=True)
    assert hard.stats.rst_sent == hard.stats.accept_queue_overflow, \
        "abort_on_overflow=1 时每次溢出都应回 RST"
    assert hard.stats.recovered_after_overflow == 0, "回 RST 的连接不可能自愈"
    assert hard.stats.accepted_by_app < slow.stats.accepted_by_app, \
        "发 RST 的版本最终建连成功的数量应更少"
    ok += 1

    # 6. SYN 洪泛：syncookies 关 → 半连接队列溢出后 SYN 被丢
    flood_off = simulate(n_clients=400, accept_every=1000, ticks=300, backlog=8,
                         somaxconn=4096, tcp_max_syn_backlog=4, syncookies=False,
                         clients_per_tick=20)
    assert flood_off.stats.syn_dropped > 0, "syncookies 关闭时应丢 SYN"
    assert flood_off.stats.max_syn_q <= 4, "半连接队列不应超过 tcp_max_syn_backlog"
    assert flood_off.stats.syncookie_issued == 0
    ok += 1

    # 7. SYN 洪泛：syncookies 开 → 不丢 SYN、不占队列（状态编码进 seq）
    flood_on = simulate(n_clients=400, accept_every=1000, ticks=300, backlog=8,
                        somaxconn=4096, tcp_max_syn_backlog=4, syncookies=True,
                        clients_per_tick=20)
    assert flood_on.stats.syn_dropped == 0, "syncookies 开启时不应丢 SYN"
    assert flood_on.stats.syncookie_issued > 0, "溢出部分应改用 SYN Cookie"
    assert flood_on.stats.max_syn_q <= 4, "SYN Cookie 不占半连接队列"
    assert flood_on.stats.syn_dropped < flood_off.stats.syn_dropped, \
        "开启 syncookies 后丢包应显著减少"
    ok += 1

    # 8. 单调性：调大有效队列上限，溢出次数必须下降（不增）
    overflows = []
    for backlog in (4, 8, 16, 64):
        r = simulate(n_clients=60, accept_every=20, ticks=4000, backlog=backlog,
                     somaxconn=4096, abort_on_overflow=False)
        overflows.append(r.stats.accept_queue_overflow)
    assert all(b <= a for a, b in zip(overflows, overflows[1:])), \
        f"队列上限调大后溢出次数不应上升: {overflows}"
    assert overflows[-1] == 0 < overflows[0], f"backlog=64 应不再溢出: {overflows}"
    ok += 1

    # 9. somaxconn 是"隐形天花板"：backlog 写 1024 但 somaxconn=128 → 有效仍是 128
    capped = simulate(n_clients=400, accept_every=500, ticks=3000, backlog=1024,
                      somaxconn=128, abort_on_overflow=False)
    big = simulate(n_clients=400, accept_every=500, ticks=3000, backlog=1024,
                   somaxconn=4096, abort_on_overflow=False)
    assert capped.stats.max_accept_q == 128, \
        f"somaxconn=128 应把队列卡在 128，实际 {capped.stats.max_accept_q}"
    assert capped.stats.accept_queue_overflow > 0, "被卡住时应发生溢出"
    assert big.stats.max_accept_q > 128 and big.stats.accept_queue_overflow == 0, \
        "somaxconn 放开后队列应能超过 128 且不再溢出"
    ok += 1

    # 10. 半连接队列的内存代价 ≈ tcp_max_syn_backlog × 304 B
    for cap in (128, 1024, 8192, 65536):
        mem_mb = cap * BYTES_PER_SYN_RECV / (1024 * 1024)
        assert mem_mb < cap, "sanity"
        if cap == 65536:
            assert 18 < mem_mb < 20, f"65536 个 SYN_RECV ≈ 19 MiB，实际算得 {mem_mb:.1f}"
    ok += 1

    print(f"[self-check] {ok}/10 项断言全部通过")
    return ok


def report() -> None:
    print("\n=== 1) 两条队列的分工（man7 listen(2) + Linux 2.2 语义变更）===")
    print("  半连接队列 (SYN queue)   : 存 SYN_RECV，上限 net.ipv4.tcp_max_syn_backlog")
    print("  全连接队列 (accept queue): 存已 ESTABLISHED、等 accept() 的连接")
    print("                            上限 min(listen backlog, net.core.somaxconn)")
    print(f"  单个 SYN_RECV 约 {BYTES_PER_SYN_RECV} 字节（内核文档）")

    print("\n=== 2) 应用消费速度 vs 全连接队列溢出（backlog=4，60 个连接）===")
    print(f"  {'accept 间隔':>12}{'建连成功':>10}{'溢出(ACK被忽略)':>16}{'自愈成功':>10}"
          f"{'RST':>6}{'最终 accept':>12}{'放弃':>6}")
    for every in (1, 5, 10, 25, 50):
        r = simulate(n_clients=60, accept_every=every, ticks=4000, backlog=4,
                     somaxconn=4096, abort_on_overflow=False)
        print(f"  {every:>12}{r.stats.established:>10}{r.stats.accept_queue_overflow:>16}"
              f"{r.stats.recovered_after_overflow:>10}{r.stats.rst_sent:>6}"
              f"{r.stats.accepted_by_app:>12}{r.stats.gave_up:>6}")
    print("  ← accept 间隔越大（应用越慢），全连接队列溢出越严重；默认策略靠重传自愈一部分，")
    print("     但重传次数有上限（tcp_synack_retries），超出就真丢了。")

    print("\n=== 3) tcp_abort_on_overflow：丢弃 vs RST 的取舍（backlog=4, accept 间隔=4）===")
    for abort in (False, True):
        r = simulate(n_clients=30, accept_every=4, ticks=4000, backlog=4,
                     somaxconn=4096, abort_on_overflow=abort)
        tag = "RST（客户端立刻 connection refused）" if abort else "忽略 ACK（重传后自愈）"
        print(f"  abort_on_overflow={int(abort)}: 溢出 {r.stats.accept_queue_overflow:>3} 次, "
              f"RST {r.stats.rst_sent:>3} 次, 自愈 {r.stats.recovered_after_overflow:>3} 个, "
              f"最终 accept {r.stats.accepted_by_app:>3} 个  ← {tag}")

    print("\n=== 4) SYN 洪泛：syncookies 的挡板作用"
          "（tcp_max_syn_backlog=4，每 tick 到达 20 个 SYN）===")
    for ck in (False, True):
        r = simulate(n_clients=400, accept_every=1000, ticks=300, backlog=8,
                     somaxconn=4096, tcp_max_syn_backlog=4, syncookies=ck,
                     clients_per_tick=20)
        print(f"  tcp_syncookies={int(ck)}: 处理 SYN {r.stats.syn_received:>4}, "
              f"丢弃 {r.stats.syn_dropped:>4}, SYN Cookie {r.stats.syncookie_issued:>3}, "
              f"半连接队列峰值 {r.stats.max_syn_q}")
    print("  注意：内核文档明确 syncookies 只是**回退机制**，不能用它支撑高负载站点的")
    print("        合法连接速率——它违反 TCP 协议、禁用 TCP 扩展，会拖累客户端与中继。")

    print("\n=== 5) somaxconn 是隐形天花板（应用两处都写 backlog=1024，400 个连接）===")
    for smc in (128, 4096):
        r = simulate(n_clients=400, accept_every=500, ticks=3000, backlog=1024,
                     somaxconn=smc, abort_on_overflow=False)
        print(f"  net.core.somaxconn={smc:>5}: 有效上限 {min(1024, smc):>5}, "
              f"队列峰值 {r.stats.max_accept_q:>4}, 溢出 {r.stats.accept_queue_overflow:>5}")
    print("  ← 只改应用层 backlog 没用：内核取 min(backlog, somaxconn)，"
          "somaxconn 才是天花板。")

    print("\n=== 6) 线上诊断命令 ===")
    for cmd, why in (
        ("ss -lnt", "Recv-Q = 当前全连接队列长度，Send-Q = 队列上限"),
        ("netstat -s | grep -i listen",
         "'times the listen queue of a socket overflowed' / 'SYNs to LISTEN sockets dropped'"),
        ("nstat -az TcpExtListenOverflows TcpExtListenDrops", "溢出与丢弃计数器"),
        ("cat /proc/sys/net/core/somaxconn", "全连接队列的隐形上限"),
        ("cat /proc/sys/net/ipv4/tcp_max_syn_backlog", "半连接队列上限"),
        ("dmesg | grep -i 'SYN flooding'", "半连接队列溢出的典型告警"),
    ):
        print(f"  {cmd:<50} # {why}")


def main() -> None:
    self_check()
    report()


if __name__ == "__main__":
    main()
