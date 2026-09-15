#!/usr/bin/env python3
"""零拷贝（zero-copy）发送路径模型：read+write / mmap+write / sendfile / splice。

不依赖 Linux 特有系统调用，用"数据搬运模型"把每种方案的
CPU 拷贝次数、DMA 拷贝次数、用户↔内核上下文切换次数、系统调用次数
算清楚（这些是 man7 与内核文档里可查的既定事实），再附一段本机
用户态拷贝的代理微基准，说明"copy 的成本随字节数线性增长，而传描述符是 O(1)"。

运行：python3 main.py      （自带断言自检，失败即非零退出）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

MSS = 1460
# RFC/man7：单次 sendfile() 最多传输 0x7ffff000 字节
SENDFILE_MAX = 0x7FFFF000


# ---------------------------------------------------------------- 搬运模型
@dataclass
class Move:
    """一次数据搬运。engine: "DMA"（硬盘/网卡控制器）或 "CPU"（memcpy）。"""

    src: str
    dst: str
    engine: str
    bytes_moved: int


@dataclass
class Scheme:
    name: str
    user_kernel_switches: int      # 用户态↔内核态切换次数（每 chunk 计）
    syscalls: int                  # 每 chunk 的系统调用次数
    moves: list[Move] = field(default_factory=list)
    notes: str = ""

    def cpu_copies(self) -> int:
        return sum(1 for m in self.moves if m.engine == "CPU")

    def dma_copies(self) -> int:
        return sum(1 for m in self.moves if m.engine == "DMA")

    def memory_bandwidth(self, size: int) -> int:
        """读写过的总字节数（内存带宽压力的代理指标）。"""
        return size * len(self.moves)


def build_schemes(size: int) -> dict[str, Scheme]:
    """构造四种发送路径。size = 一个 chunk 的字节数。"""
    return {
        "read+write": Scheme(
            "read+write", user_kernel_switches=4, syscalls=2,
            moves=[
                Move("disk", "page_cache", "DMA", size),
                Move("page_cache", "user_buf", "CPU", size),
                Move("user_buf", "socket_buf", "CPU", size),
                Move("socket_buf", "nic", "DMA", size),
            ],
            notes="教科书路径；用户态缓冲区只为转发而存在，两次 CPU 拷贝是纯税",
        ),
        "mmap+write": Scheme(
            "mmap+write", user_kernel_switches=4, syscalls=2,
            moves=[
                Move("disk", "page_cache", "DMA", size),
                Move("page_cache", "socket_buf", "CPU", size),
                Move("socket_buf", "nic", "DMA", size),
            ],
            notes="把 page cache 映射进用户地址空间，省掉 1 次 CPU 拷贝；"
                  "但切换次数没变，且缺页中断 + TLB 刷新有额外成本",
        ),
        "sendfile": Scheme(
            "sendfile", user_kernel_switches=2, syscalls=1,
            moves=[
                Move("disk", "page_cache", "DMA", size),
                Move("page_cache", "socket_buf", "CPU", size),
                Move("socket_buf", "nic", "DMA", size),
            ],
            notes="只传描述符/页引用，数据全程在内核；网卡不支持 SG-DMA 时"
                  "仍有 1 次 CPU 拷贝",
        ),
        "sendfile+SG-DMA": Scheme(
            "sendfile+SG-DMA", user_kernel_switches=2, syscalls=1,
            moves=[
                Move("disk", "page_cache", "DMA", size),
                Move("page_cache", "nic", "DMA", size),
            ],
            notes="网卡支持 scatter-gather DMA：CPU 只把'地址+长度'描述符"
                  "写进 socket 缓冲区，真正的 0 CPU 拷贝",
        ),
        "splice": Scheme(
            "splice", user_kernel_switches=2, syscalls=2,
            moves=[
                Move("disk", "page_cache", "DMA", size),
                Move("page_cache", "nic", "DMA", size),
            ],
            notes="经管道搬运页引用，0 CPU 拷贝且不需要网卡硬件支持；"
                  "代价是两端至少有一端必须是管道，每 chunk 要 2 次调用",
        ),
    }


# ---------------------------------------------------------------- 大文件分块
def chunked_sendfile(size: int, chunk: int) -> list[tuple[int, int]]:
    """把一个 size 字节的传输拆成 sendfile() 调用序列，返回 (offset, count)。

    man7 sendfile(2)：offset 非 NULL 时函数会把它更新为"最后一个被读取字节之后"
    的位置，且不修改 in_fd 的文件偏移；单次调用上限 0x7ffff000。
    生产代码必须循环到全部发完（大文件一次性 sendfile 会长时间占住网卡发送队列）。
    """
    calls, offset = [], 0
    while offset < size:
        n = min(chunk, size - offset, SENDFILE_MAX)
        calls.append((offset, n))
        offset += n
        # 通知内核后续还有数据，减少包数（man7 tcp(7) 的 TCP_MORE 语义）
        if len(calls) > 10_000_000:
            raise RuntimeError("分块循环失控")
    return calls


class SendfileError(Exception):
    """模拟 sendfile 的 errno。"""


def sendfile_check(in_fd_kind: str, out_fd_kind: str, count: int,
                   offset_is_seekable: bool = True) -> None:
    """按 man7 sendfile(2) 的约束做前置校验，违反即抛异常。"""
    if in_fd_kind == "socket":
        # "The in_fd argument must correspond to a file which supports
        #  mmap(2)-like operations (i.e., it cannot be a socket)."
        raise SendfileError("EINVAL: in_fd 不支持 mmap-like 操作（socket 不行）")
    if count < 0:
        raise SendfileError("EINVAL: count 为负")
    if count > SENDFILE_MAX:
        raise SendfileError("EOVERFLOW: count 超过 0x7ffff000")
    if out_fd_kind != "pipe" and out_fd_kind not in ("socket", "file"):
        raise SendfileError("EBADF: out_fd 类型不支持")
    if not offset_is_seekable:
        raise SendfileError("ESPIPE: in_fd 不可 seek 但传了非 NULL offset")


# ---------------------------------------------------------------- 代理微基准
def user_copy_benchmark(total: int = 32 * 1024 * 1024, chunk: int = 64 * 1024):
    """本机微基准：把 total 字节"搬过用户态缓冲区" vs 只传引用。

    这不是内核真实路径（真机要用 strace/perf 量），而是为了说明量级差异：
    拷贝成本 ∝ 字节数，传描述符的成本 ∝ chunk 数 × O(1)。
    """
    src = bytearray(chunk)
    dst = bytearray(chunk)
    refs = []

    t0 = time.perf_counter()
    for _ in range(total // chunk):
        dst[:] = src                      # 一次真实的用户态 memcpy
    copy_secs = time.perf_counter() - t0

    view = memoryview(src)
    t1 = time.perf_counter()
    for _ in range(total // chunk):
        refs.append((view, len(view)))    # 只登记描述符，不搬数据
    ref_secs = time.perf_counter() - t1

    copy_bw = total / copy_secs / 1e9
    ref_bw = total / max(ref_secs, 1e-9) / 1e9
    return copy_secs, ref_secs, copy_bw, ref_bw


# ---------------------------------------------------------------- 自检
