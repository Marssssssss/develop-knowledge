#!/usr/bin/env python3
"""backlog / SYN 队列自检：场景编排与断言（模型在 backlog_model.py）。

运行：python3 main.py      （自带断言自检，失败即非零退出）
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backlog_model import *  # noqa: E402,F401,F403
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
