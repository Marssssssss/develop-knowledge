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
def self_check() -> int:
    ok = 0
    size = 64 * 1024
    s = build_schemes(size)

    # 1. 传统 read+write 是最"贵"的：2 次 CPU 拷贝 + 4 次上下文切换
    assert s["read+write"].cpu_copies() == 2, "read+write 应有 2 次 CPU 拷贝"
    assert s["read+write"].user_kernel_switches == 4, "read+write 应 4 次切换"
    ok += 1

    # 2. sendfile 把切换压到 2 次、系统调用 1 次（数据不再进用户态）
    sf = s["sendfile"]
    assert sf.user_kernel_switches == 2 and sf.syscalls == 1, "sendfile 应为 2 切换/1 系统调用"
    assert sf.cpu_copies() == 1, "无 SG-DMA 时 sendfile 仍有 1 次 CPU 拷贝"
    ok += 1

    # 3. sendfile + SG-DMA：0 CPU 拷贝（CPU 只写描述符）
    assert s["sendfile+SG-DMA"].cpu_copies() == 0, "SG-DMA 下应为 0 CPU 拷贝"
    assert s["sendfile+SG-DMA"].dma_copies() == 2, "SG-DMA 下应为 2 次 DMA 拷贝"
    ok += 1

    # 4. splice：0 CPU 拷贝，且不依赖网卡硬件（真·通用零拷贝）
    assert s["splice"].cpu_copies() == 0, "splice 应为 0 CPU 拷贝"
    assert s["splice"].syscalls == 2, "splice 需经管道，每 chunk 2 次调用"
    ok += 1

    # 5. mmap+write 省 1 次 CPU 拷贝，但切换次数与 read+write 相同
    mm = s["mmap+write"]
    assert mm.cpu_copies() == 1 and mm.user_kernel_switches == 4, "mmap 应省拷贝不省切换"
    assert mm.cpu_copies() == sf.cpu_copies() and \
        mm.user_kernel_switches > sf.user_kernel_switches, \
        "mmap 与 sendfile CPU 拷贝相同，但切换更多 → 只发文件时选 sendfile"
    ok += 1

    # 6. 内存带宽压力单调下降：read+write > mmap == sendfile > SG-DMA == splice
    bw = {k: v.memory_bandwidth(size) for k, v in s.items()}
    assert bw["read+write"] > bw["sendfile"] == bw["mmap+write"] > bw["splice"], \
        f"内存带宽排序异常: {bw}"
    assert bw["sendfile+SG-DMA"] <= bw["sendfile"] / 1.4, "SG-DMA 应明显更省带宽"
    ok += 1

    # 7. 相对 read+write 的节省量（README 引用的数字）
    assert s["read+write"].memory_bandwidth(size) == 4 * size
    assert s["sendfile"].memory_bandwidth(size) == 3 * size
    assert s["splice"].memory_bandwidth(size) == 2 * size
    saved_cpu = s["read+write"].cpu_copies() - s["sendfile"].cpu_copies()
    saved_switch = s["read+write"].user_kernel_switches - s["sendfile"].user_kernel_switches
    assert saved_cpu == 1 and saved_switch == 2, "sendfile 相对 read+write 应省 1 拷贝 2 切换"
    ok += 1

    # 8. sendfile 的 in_fd 不能是 socket；非法 count 要报 EOVERFLOW
    try:
        sendfile_check("socket", "socket", 1024)
        raise AssertionError("in_fd 为 socket 时应报 EINVAL")
    except SendfileError as e:
        assert "EINVAL" in str(e)
    try:
        sendfile_check("file", "socket", SENDFILE_MAX + 1)
        raise AssertionError("count 超限时应报 EOVERFLOW")
    except SendfileError as e:
        assert "EOVERFLOW" in str(e)
    try:
        sendfile_check("file", "socket", 1024, offset_is_seekable=False)
        raise AssertionError("不可 seek 时应报 ESPIPE")
    except SendfileError as e:
        assert "ESPIPE" in str(e)
    ok += 1

    # 9. 大文件必须分块：每次调用 ≤ 2MB，offset 严格推进且覆盖全部字节
    total, chunk = 10 * 1024 * 1024, 2 * 1024 * 1024
    calls = chunked_sendfile(total, chunk)
    assert len(calls) == 5, f"10MB / 2MB 应分 5 次，实际 {len(calls)}"
    assert calls[0] == (0, chunk) and calls[-1][0] + calls[-1][1] == total, \
        "offset 序列应覆盖 [0, total)"
    for i in range(1, len(calls)):
        assert calls[i][0] == calls[i - 1][0] + calls[i - 1][1], "offset 必须严格推进"
    assert sum(n for _, n in calls) == total, "分块总量应等于文件大小"
    ok += 1

    # 10. 单次上限 0x7ffff000：32 位/64 位系统都一样，超过就得拆
    big = chunked_sendfile(SENDFILE_MAX + 1000, 1 << 30)
    assert big[0][1] <= SENDFILE_MAX and all(n <= SENDFILE_MAX for _, n in big)
    assert sum(n for _, n in big) == SENDFILE_MAX + 1000, "超限时应拆成多次而非截断"
    assert len(big) == 2, "超限 1000 字节应拆成 2 次调用"
    ok += 1

    print(f"[self-check] {ok}/10 项断言全部通过")
    return ok


def print_table() -> None:
    size = 1 << 20  # 以 1 MiB 为 chunk 计量单位，便于看内存搬运总量
    s = build_schemes(size)
    print("（每传输 1 MiB 数据；内存搬运量 = 搬运次数 × 1 MiB）")
    print(f"{'方案':<17}{'CPU拷贝':>8}{'DMA拷贝':>9}{'上下文切换':>11}{'系统调用':>9}"
          f"{'内存搬运量':>12}")
    for key in ("read+write", "mmap+write", "sendfile", "sendfile+SG-DMA", "splice"):
        v = s[key]
        print(f"{v.name:<17}{v.cpu_copies():>8}{v.dma_copies():>9}"
              f"{v.user_kernel_switches:>11}{v.syscalls:>9}"
              f"{v.memory_bandwidth(size) // (1 << 20):>9} MiB")
    print("\n逐方案说明：")
    for key in ("read+write", "mmap+write", "sendfile", "sendfile+SG-DMA", "splice"):
        print(f"  · {s[key].name}: {s[key].notes}")
        for m in s[key].moves:
            print(f"      [{m.engine}] {m.src} -> {m.dst}")


def main() -> None:
    self_check()

    print("\n=== 1) 五种发送路径的搬运成本（每 1 个 chunk，SMSS=1460 B）===")
    print_table()

    print("\n=== 2) 大文件必须分块（man7：单次上限 0x7ffff000，且不要一次性 "
          "sendfile 塞满网卡队列）===")
    for total_mb, chunk_kb in ((10, 2048), (100, 1024), (1024, 4096)):
        calls = chunked_sendfile(total_mb * 1024 * 1024, chunk_kb * 1024)
        print(f"  {total_mb:>5} MiB 文件 / {chunk_kb:>5} KiB 分块 → {len(calls):>5} 次 sendfile()")

    print("\n=== 3) 本机用户态拷贝微基准（32 MiB，代理指标非内核真实路径）===")
    copy_secs, ref_secs, copy_bw, ref_bw = user_copy_benchmark()
    print(f"  逐块 memcpy 过用户态缓冲 : {copy_secs:7.4f} s  ({copy_bw:7.2f} GB/s)")
    print(f"  只登记 (view, len) 描述符: {ref_secs:7.4f} s  ({ref_bw:7.2f} GB/s)")
    print(f"  → 拷贝成本随字节数线性增长；零拷贝路径上 CPU 只处理 O(chunk 数) 的描述符。")

    print("\n=== 4) 什么时候不能用零拷贝 ===")
    for scene, why in (
        ("HTTPS 需要加密", "数据必须进用户态做 TLS（除非用内核 kTLS）"),
        ("gzip / 图片缩放 / 模板替换", "必须改字节，绕不开用户态缓冲"),
        ("代理转发 socket→socket", "sendfile 的 in_fd 不能是 socket，改用 splice+管道"),
        ("需要按业务字段过滤/改写", "sendfile 是黑盒，数据进了内核就摸不到"),
    ):
        print(f"  · {scene:<24} {why}")


if __name__ == "__main__":
    main()
