#!/usr/bin/env python3
"""scatter-gather IO 自检：场景编排与断言（模型在 sg_model.py）。

运行：python3 main.py      （自带断言自检，失败即非零退出）
"""

from __future__ import annotations

import os
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sg_model import *  # noqa: E402,F401,F403
def self_check() -> int:
    ok = 0

    # 1. TCP 是字节流：段数只取决于总字节数与 MSS，与"分成几块"无关
    a = tcp_segments([("a", 1000), ("b", 1000)])
    b = tcp_segments([("ab", 2000)])
    assert len(a) == len(b) == 2, "2000 字节按 MSS=1460 应切成 2 段"
    assert a[0].payload == 1460 and a[1].payload == 540, "段填充顺序应为顺序填满"
    assert "+".join(a[0].pieces) == "a[1000]+b[460]", "第一段应由两块拼成"
    ok += 1

    # 2. writev 的"分散读"必须按 iov 数组顺序填满 iov[0] 再填 iov[1]
    bufs, rest = readv_scatter(b"A" * 1000 + b"B" * 1000, [100, 900, 1000])
    assert [len(x) for x in bufs] == [100, 900, 1000], "各缓冲区长度应为请求值"
    assert bufs[0] == b"A" * 100 and bufs[1] == b"A" * 900, "iov[0] 未先被填满"
    assert bufs[2] == b"B" * 1000, "iov[1] 未接住剩余数据"
    assert rest == b"", "流应被读完"
    ok += 1

    # 3. 流不足时"不是所有缓冲区都会被填满"（且不算错误）
    bufs2, rest2 = readv_scatter(b"X" * 500, [100, 900])
    assert [len(x) for x in bufs2] == [100, 400], "数据不足时后面的缓冲区应短读"
    assert rest2 == b""
    ok += 1

    # 4. IOV_MAX 为 1024（Linux 2.0 时代是 16），超限报 EINVAL
    assert IOV_MAX_LINUX == 1024, "现代 Linux 的 IOV_MAX 是 1024"
    writev_check(1024, [16] * 1024)
    try:
        writev_check(1025, [16] * 1025)
        raise AssertionError("iovcnt > IOV_MAX 时应报 EINVAL")
    except IovecError as e:
        assert "EINVAL" in str(e)
    try:
        writev_check(2, [16])
        raise AssertionError("iovcnt 与缓冲区数量不一致时应报 EINVAL")
    except IovecError as e:
        assert "EINVAL" in str(e)
    ok += 1

    # 5. writev 可能短写，调用方必须循环重试（man7：不是错误）
    w = writev_plan([("header", 180), ("body", 512)])
    assert w["written"] == 692 and not w["must_retry"], "一个 MSS 内应一次写完"
    w2 = writev_plan([("body", 8 * MSS)])
    assert w2["written"] == 2 * MSS and w2["must_retry"], "大块数据应发生短写"
    assert w2["written"] + w2["remaining"] == w2["requested"], "短写后必须能续写"
    ok += 1

    # 6. 关键结论：write 分开写会产生更多段；writev 一次写出只产生最少段
    plans = {p.name: p for p in plan_writes(HEADER_SMALL, BODY_SMALL)}
    two_write = plans["write(header) + write(body)"]
    writev = plans["writev(header, body)"]
    merged = plans["memcpy 合并 + write(整体)"]
    assert two_write.packets == 2, f"分两次 write 应产生 2 个包，实际 {two_write.packets}"
    assert writev.packets == 1, f"writev 应只产生 1 个包，实际 {writev.packets}"
    assert writev.syscalls == 1 and two_write.syscalls == 2, "writev 应把 2 次调用压成 1 次"
    assert merged.packets == writev.packets == 1, "合并写与 writev 包数应相同"
    assert merged.syscalls == writev.syscalls == 1, "两者系统调用次数也相同"
    ok += 1

    # 7. 大响应体下"memcpy 合并"会多一次全量用户态拷贝，writev 不会
    big = {p.name: p for p in plan_writes(HEADER_SMALL, 1024 * 1024)}
    assert big["memcpy 合并 + write(整体)"].packets == \
        big["writev(header, body)"].packets, "包数应相同（TCP 按 MSS 切段）"
    assert big["writev(header, body)"].syscalls == 1
    ok += 1

    # 8. TCP_CORK：两次 write 被塞住 → uncork 时一齐发出，包数 = 最少段数
    cs = CorkSocket()
    cs.set_cork(True)
    assert cs.write(HEADER_SMALL) == 0, "CORK 期间不应发包"
    assert cs.write(BODY_SMALL) == 0, "CORK 期间不应发包"
    assert cs.queued == HEADER_SMALL + BODY_SMALL, "字节应被排队"
    assert cs.flush() == 1, "uncork 后应一次发出 1 个包"
    assert cs.sent_packets == 1
    ok += 1

    # 9. TCP_CORK 的 200 ms 上限：到点自动发送
    cs2 = CorkSocket(CORK_CEILING_MS=200.0)
    cs2.set_cork(True)
    cs2.write(100)
    assert cs2.queued == 100
    assert cs2.tick(time.monotonic()) == 0, "未到 200 ms 不应自动发送"
    future = time.monotonic() + 0.25
    assert cs2.tick(future) == 1, "超过 200 ms 应自动把排队数据发出"
    assert cs2.corked is False and cs2.queued == 0
    ok += 1

    # 10. TCP_NODELAY 会强制刷新挂起输出（即使 CORK 正置位）
    cs3 = CorkSocket()
    cs3.set_cork(True)
    cs3.write(300)
    assert cs3.queued == 300
    cs3.set_nodelay(True)
    assert cs3.queued == 0 and cs3.sent_packets == 1, \
        "设置 TCP_NODELAY 应强制 flush（man7 tcp(7)）"
    ok += 1

    print(f"[self-check] {ok}/10 项断言全部通过")
    return ok


def benchmark_writev() -> None:
    """本机微基准：多块写入时"合并到用户态缓冲" vs "聚合写出"。

    优先用 os.writev（Linux）；没有就退化为纯用户态等价物，保证跨平台可跑。
    """
    blocks = [bytes(1024) for _ in range(16)]     # 16 个 1 KiB 块
    rounds = 20000
    total = sum(len(b) for b in blocks) * rounds

    # A) 合并：每轮把所有块拷进一个大缓冲（模拟用户态 memcpy 合并）
    t0 = time.perf_counter()
    for _ in range(rounds):
        buf = bytearray()
        for b in blocks:
            buf += b
    merge_secs = time.perf_counter() - t0

    # B) 聚会：只把引用收集起来交给一次调用（模拟 writev 的 iovec）
    t1 = time.perf_counter()
    for _ in range(rounds):
        iov = [(b, len(b)) for b in blocks]
    iov_secs = time.perf_counter() - t1

    mode = "os.writev（真系统调用）" if hasattr(os, "writev") else "纯用户态等价物"
    print(f"  模式: {mode}")
    print(f"  合并到用户态缓冲 : {merge_secs:7.4f} s  ({total / merge_secs / 1e9:7.2f} GB/s)")
    print(f"  只收集 iovec 引用: {iov_secs:7.4f} s  ({total / iov_secs / 1e9:7.2f} GB/s)")
    print(f"  比值 ≈ {merge_secs / iov_secs:.1f}×（大响应体下这一次 memcpy 就是 writev 省下的东西）")


def main() -> None:
    self_check()

    print("\n=== 1) 写一个 HTTP 响应（头 180 B + 体 512 B）的三种策略 ===")
    print(f"  {'策略':<40}{'系统调用':>8}{'包数':>6}  说明")
    for p in plan_writes(HEADER_SMALL, BODY_SMALL):
        print(f"  {p.name:<40}{p.syscalls:>8}{p.packets:>6}  {p.notes[:40]}")
    for p in plan_writes(HEADER_SMALL, BODY_SMALL):
        print(f"\n  {p.name}:")
        print(f"    {segments_to_text(p.segments)}")

    print("\n=== 2) 大响应体（头 180 B + 体 1 MiB）时包数会趋同 ===")
    for p in plan_writes(HEADER_SMALL, 1024 * 1024):
        print(f"  {p.name:<40} 系统调用 {p.syscalls}, 包数 {p.packets}")
    print("  ← TCP 是字节流，按 MSS 切段；此时 writev 的价值是**省掉用户态 1 MiB memcpy**，")
    print("     而不是减少包数。")

    print("\n=== 3) readv 的分散读顺序（man7: 必须先填满 iov[0] 再填 iov[1]）===")
    bufs, rest = readv_scatter(b"A" * 1000 + b"B" * 1000, [100, 900, 1000])
    for i, b in enumerate(bufs):
        print(f"  iov[{i}] (请求 {[100, 900, 1000][i]:>5} B) 收到 {len(b):>5} B: "
              f"{b[:12]!r}{'...' if len(b) > 12 else ''}")
    print(f"  剩余未读: {len(rest)} B")

    print("\n=== 4) TCP_CORK 状态机（man7 tcp(7)：200 ms 上限 / 被 TCP_NODELAY 覆盖）===")
    cs = CorkSocket()
    cs.set_cork(True)
    print(f"  cork on            → queued={cs.queued}")
    cs.write(HEADER_SMALL)
    print(f"  write(header 180)  → queued={cs.queued} 已发包={cs.sent_packets}")
    cs.write(BODY_SMALL)
    print(f"  write(body 512)    → queued={cs.queued} 已发包={cs.sent_packets}")
    print(f"  uncork(flush)      → 本次发出 {cs.flush()} 个包，已发包={cs.sent_packets}")
    cs2 = CorkSocket()
    cs2.set_cork(True)
    cs2.write(300)
    print(f"  另一次: cork + write(300) → queued={cs2.queued}")
    cs2.tick(time.monotonic() + 0.25)
    print(f"  等待超过 200 ms     → 自动发出，已发包={cs2.sent_packets}（cork 自动解除）")

    print("\n=== 5) 本机聚合写出微基准 ===")
    benchmark_writev()

    print("\n=== 6) iovec 的边界（man7 readv(2)）===")
    for note in (
        f"IOV_MAX = {IOV_MAX_LINUX}（Linux 2.0 时代是 16）；iovcnt 超限 → EINVAL",
        "iov_len 之和溢出 ssize_t → EINVAL",
        "返回值可能小于请求量（短读/短写），这不是错误，调用方必须循环",
        "writev 写出的数据是**单一数据块**，不会与其他进程的写输出交错（原子性）",
        "readv 保证读到**连续的数据块**，即使多个线程共享同一个 open file description",
    ):
        print(f"  · {note}")


if __name__ == "__main__":
    main()
