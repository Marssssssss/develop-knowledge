#!/usr/bin/env python3
"""scatter-gather IO：readv/writev 与 TCP_CORK 组包。

两件事：
  1. **分散/聚集**：readv 把一次读分散进多个缓冲区，writev 把多个缓冲区的数据
     用**一次系统调用**聚集写出（man7：writev 写出的数据是一个不可与其他进程输出
     交错的单一数据块，即原子写）。IOV_MAX 在 Linux 上是 1024（Linux 2.0 时代是 16）。
  2. **包数控制**：把"响应头 + 正文"分两次 write 会发出两个小包（Nagle/延迟 ACK 下
     还可能更糟）；用 writev 一次写出，或用 TCP_CORK 把部分帧"塞住"到清除时一齐发出，
     都能把包数压到 1。man7 tcp(7)：TCP_CORK 有 200 ms 上限，达到后自动发送。

不依赖 socket：用"系统调用计数 + 段填充模型"算清楚每种写法的调用次数与包数，
再附一段本机真实的 writev 等价微基准（用 os.writev 若可用，否则退化为模型）。

运行：python3 main.py      （自带断言自检，失败即非零退出）
"""

from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass

MSS = 1460                # 单个 TCP 段能装的净荷上限
HEADER_SMALL = 180        # 典型 HTTP 响应头（小）
BODY_SMALL = 512          # 典型小响应体
IOV_MAX_LINUX = 1024      # man7 readv(2)：现代 Linux 的 iovec 数量上限


# ------------------------------------------------------------ 段填充模型
@dataclass
class Segment:
    """一个 TCP 段：装了多少净荷、由哪几块数据拼成。"""

    payload: int
    pieces: list[str]


def tcp_segments(chunks: list[tuple[str, int]], mss: int = MSS) -> list[Segment]:
    """按"顺序写字节"的方式把若干 (名称, 长度) 的数据块切成 TCP 段。

    TCP 是**字节流**协议：内核按 MSS 切段，不关心应用调用了几次 write。
    所以"包数"取决于**写入节奏**（是否有 cork / 是否合并），而不是数据分成了几块。
    """
    segs: list[Segment] = []
    cur = Segment(payload=0, pieces=[])
    for name, size in chunks:
        left = size
        while left > 0:
            room = mss - cur.payload
            take = min(room, left)
            cur.pieces.append(f"{name}[{take}]")
            cur.payload += take
            left -= take
            if cur.payload == mss:
                segs.append(cur)
                cur = Segment(payload=0, pieces=[])
    if cur.payload > 0:
        segs.append(cur)
    return segs


@dataclass
class Plan:
    name: str
    syscalls: int             # 写这批数据需要的系统调用次数
    segments: list[Segment]
    notes: str

    @property
    def packets(self) -> int:
        return len(self.segments)


def plan_writes(header: int, body: int) -> list[Plan]:
    """三种"写响应"策略的对照。

    注意这里刻意**不**假设内核会做 tcp_autocorking（Linux 3.14+ 默认开启，会在
    "前一个包还排在 qdisc 里"时自动合并连续小写）。关掉它、或写得很稀疏时，
    分两次 write 就是两个小包——这正是要对比的糟糕基线。
    """
    chunks = [("header", header), ("body", body)]
    plans = []

    # A) 两次 write：头一个段、正文一个段（小响应最糟的写法）
    plans.append(Plan(
        name="write(header) + write(body)",
        syscalls=2,
        segments=tcp_segments([("header", header)]) + tcp_segments([("body", body)]),
        notes="两次独立 write：头独占一个段，正文另起段。TCP_NODELAY 下立即两个小包；"
              "Nagle 开启时第二个小包要等 ACK，反而引入延迟。",
    ))

    # B) 合并到用户态缓冲区再一次 write：1 次调用
    plans.append(Plan(
        name="memcpy 合并 + write(整体)",
        syscalls=1,
        segments=tcp_segments([("merged", header + body)]),
        notes="用户态先拼成一个缓冲区再写：调用少、包少，代价是一次额外的用户态 memcpy"
              "（大响应体时这个拷贝很贵）。",
    ))

    # C) writev：一次系统调用聚集写出，无需用户态拷贝
    plans.append(Plan(
        name="writev(header, body)",
        syscalls=1,
        segments=tcp_segments(chunks),
        notes="一次系统调用写出多块，内核直接按 iovec 组装段——既有合并写的包数，"
              "又省掉用户态 memcpy。HTTP 服务器写「头 + 小正文」的标准做法。",
    ))

    # D) write(header) + TCP_CORK + write(body) + uncork
    plans.append(Plan(
        name="TCP_CORK: write(header)+write(body)+uncork",
        syscalls=3,   # 2 次 write + 1 次 setsockopt 解除
        segments=tcp_segments(chunks),
        notes="把部分帧塞住到 uncork 再一齐发出（man7: 200 ms 上限后自动发送）。"
              "适合「头在别处产生、正文走 sendfile」的场景——C 版 demo 就是这个用法。",
    ))
    return plans


def segments_to_text(segs: list[Segment]) -> str:
    return " | ".join(f"seg{p}:{'+'.join(s.pieces)}" for p, s in enumerate(segs))


# ------------------------------------------------------------ iovec 边界
class IovecError(Exception):
    """模拟内核在 iovec 参数非法时返回的 errno。"""


def writev_check(iovcnt: int, lens: list[int]) -> None:
    """按 man7 readv(2) 的规则做前置校验。"""
    if iovcnt < 0 or iovcnt > IOV_MAX_LINUX:
        raise IovecError(f"EINVAL: iovcnt={iovcnt} 超过 IOV_MAX={IOV_MAX_LINUX}")
    if iovcnt != len(lens):
        raise IovecError("EINVAL: iovcnt 与实际缓冲区数量不一致")
    total = sum(lens)
    if total > 2 ** 63 - 1:
        raise IovecError("EINVAL: iov_len 之和溢出 ssize_t")
    if any(n < 0 for n in lens):
        raise IovecError("EINVAL: iov_len 不能为负")


def readv_scatter(stream: bytes, bufs: list[int]) -> tuple[list[bytes], bytes]:
    """模拟 readv 的"分散读"：按数组顺序**填满 iov[0] 再填 iov[1]**……

    man7: "Buffers are processed in array order. This means that readv()
    completely fills iov[0] before proceeding to iov[1]"。返回 (各缓冲区内容, 剩余流)。
    """
    out, pos = [], 0
    for n in bufs:
        out.append(stream[pos:pos + n])
        pos += n
    return out, stream[pos:]


def writev_plan(chunks: list[tuple[str, int]], mss: int = MSS) -> dict:
    """writev 的返回值语义：可能短写，必须循环。"""
    total = sum(n for _, n in chunks)
    # 模拟一次只写出前两个段（短写）
    written = min(total, 2 * MSS)
    return {"requested": total, "written": written, "remaining": total - written,
            "must_retry": written < total}


# ------------------------------------------------------------ TCP_CORK 状态机
@dataclass
class CorkSocket:
    """TCP_CORK + TCP_NODELAY 的交互（man7 tcp(7)）。"""

    corked: bool = False
    nodelay: bool = False
    queued: int = 0               # 被"塞住"尚未发出的字节
    sent_packets: int = 0
    cork_deadline: float | None = None
    CORK_CEILING_MS: float = 200.0

    def set_cork(self, on: bool) -> None:
        if on:
            self.corked = True
            self.cork_deadline = time.monotonic() + self.CORK_CEILING_MS / 1000
        else:
            self.corked = False
            self.flush()

    def set_nodelay(self, on: bool) -> None:
        # man7: "This option is overridden by TCP_CORK; however, setting this
        #  option forces an explicit flush of pending output, even if TCP_CORK
        #  is currently set."
        self.nodelay = on
        if on:
            self.flush()

    def write(self, nbytes: int, pipe_mss: int = MSS) -> int:
        """写入 nbytes；返回本次真正发出的包数。"""
        if self.corked and self.nodelay:
            self.queued += nbytes
            return 0
        if self.corked:
            self.queued += nbytes
            return 0
        packets = (nbytes + pipe_mss - 1) // pipe_mss
        self.sent_packets += packets
        return packets

    def flush(self) -> int:
        if self.queued == 0:
            return 0
        packets = (self.queued + MSS - 1) // MSS
        self.sent_packets += packets
        self.queued = 0
        self.cork_deadline = None
        return packets

    def tick(self, now: float) -> int:
        """检查 200 ms 上限：到点自动发送。"""
        if self.corked and self.cork_deadline and now >= self.cork_deadline:
            self.corked = False
            return self.flush()
        return 0


# ------------------------------------------------------------ 自检
