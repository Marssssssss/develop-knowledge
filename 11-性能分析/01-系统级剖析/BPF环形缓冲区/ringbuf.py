"""BPF ring buffer（BPF_MAP_TYPE_RINGBUF）—— 模型层。

权威来源：Linux 内核文档 "BPF ring buffer"
https://docs.kernel.org/bpf/ringbuf.html

文档确立的事实（README §2 逐条对应）：
1. 造它就是为了 perf buffer 解决不了的两件事：
   a) **跨 CPU 共享**一块缓冲区 ⇒ 内存利用率更高；
   b) **跨 CPU 也能保序**（如 fork/exec/exit）。两者都源于 perf buffer 的
      per-CPU 设计，而 MPSC 环形缓冲一次解决。
2. 它是个 map：key/value size 强制为 0，`max_entries` 是缓冲区大小且**必须是 2 的幂**。
3. 与 perf buffer 的共性：变长记录、空间不足时 reserve **失败而不阻塞**、
   可 mmap 的数据区、epoll 通知、也能忙轮询。
4. 两套 API：`bpf_ringbuf_output()` 多一次拷贝但长度可不被验证器预知；
   `reserve()/commit()/discard()` 零拷贝，但 reserve 的长度必须是验证器可
   判定的常量（否则无法保证不越界）。
5. 每条记录有 **8 字节头**：长度 + busy 位 + discard 位，另外还编码了
   「记录相对数据区起始的偏移（以页为单位）」—— 所以 commit/discard 只要
   记录指针就能反推出整个 ring buffer 的位置。
6. reserve 在自旋锁下串行推进 producer ⇒ **保留顺序严格有序**；commit 完全
   无锁且相互独立。**记录按保留顺序对消费者可见，但必须等它前面所有记录都
   提交完**。慢生产者会挡住后面已提交的记录。
7. NMI 上下文里 reserve 可能拿不到自旋锁而失败（即使缓冲区没满）。
8. 数据区在虚拟内存里**连续映射两遍** ⇒ 绕回的记录在虚拟地址上仍然连续。
9. **自节流通知**：commit 只在「消费者已经追到这条记录」时才发通知；否则
   消费者反正还要往前走，不需要额外唤醒。这让 perf buffer 时代
   "每 N 个样本通知一次"的技巧不再必要。
10. `bpf_ringbuf_query()` 支持 BPF_RB_AVAIL_DATA / RING_SIZE / CONS_POS /
    PROD_POS，返回值只是瞬时快照。

关于 8 字节头的**位布局**：文档只说明"长度 + busy 位 + discard 位 + 页偏移"，
未给出精确位号；本 demo 采用 4B len（最高位 busy、次高位 discard）+ 4B pg_off
的布局，并在 README 中标注这是 demo 布局而非内核位号。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

HDR_SIZE = 8
PAGE_SIZE = 4096

BUSY_BIT = 1 << 31
DISCARD_BIT = 1 << 30
LEN_MASK = (1 << 30) - 1

# bpf_ringbuf_query() 的四种查询
RB_AVAIL_DATA = 0
RB_RING_SIZE = 1
RB_CONS_POS = 2
RB_PROD_POS = 3

# 通知控制标志
RB_NO_WAKEUP = 1 << 0
RB_FORCE_WAKEUP = 1 << 1


def align8(n: int) -> int:
    """记录按 8 字节对齐（文档：头部 8 字节，记录长度前缀）。"""
    return (n + 7) & ~7


def is_pow2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


@dataclass
class Record:
    pos: int          # 记录头在逻辑地址空间中的起点
    length: int       # 载荷长度
    total: int        # 头部 + 对齐后载荷
    state: str        # reserved / committed / discarded
    pg_off: int
    payload: bytes = b""


class RingBuf:
    """单生产者可见语义的 MPSC ring buffer 简化模型。"""

    def __init__(self, max_entries: int):
        if not is_pow2(max_entries):
            raise ValueError(f"max_entries 必须是 2 的幂: {max_entries}")
        self.size = max_entries
        self.data = bytearray(max_entries)
        self.producer = 0       # 所有生产者**已保留**的数据总量
        self.consumer = 0       # 消费者已消费到的位置
        self.records: Dict[int, Record] = {}
        self.lock_held = False  # 模拟 reserve 的自旋锁
        self.notifications = 0

    # ------------------------------------------------------------ 头部编解码 ---
    def _put_hdr(self, pos: int, length: int, flags: int) -> int:
        off = pos & (self.size - 1)
        pg_off = off // PAGE_SIZE
        word = (length & LEN_MASK) | flags
        for i in range(4):
            self.data[(off + i) & (self.size - 1)] = (word >> (8 * i)) & 0xFF
        for i in range(4):
            self.data[(off + 4 + i) & (self.size - 1)] = (pg_off >> (8 * i)) & 0xFF
        return pg_off

    def _get_hdr(self, pos: int) -> Tuple[int, int, int]:
        off = pos & (self.size - 1)
        word = 0
        for i in range(4):
            word |= self.data[(off + i) & (self.size - 1)] << (8 * i)
        pg = 0
        for i in range(4):
            pg |= self.data[(off + 4 + i) & (self.size - 1)] << (8 * i)
        return word & LEN_MASK, word & (BUSY_BIT | DISCARD_BIT), pg

    # ---------------------------------------------------------------- 生产 ---
    def reserve(self, length: int, nmi: bool = False) -> Optional[int]:
        """预留一段空间；返回记录头指针（这里是逻辑位置），失败返回 None。"""
        if nmi and self.lock_held:
            return None  # NMI 里拿不到自旋锁 ⇒ 即使没满也失败
        total = align8(HDR_SIZE + length)
        if self.producer - self.consumer + total > self.size:
            return None  # 空间不足：**不阻塞**，直接失败
        self.lock_held = True
        pos = self.producer
        self.producer += total
        pg = self._put_hdr(pos, length, BUSY_BIT)
        self.records[pos] = Record(pos=pos, length=length, total=total,
                                   state="reserved", pg_off=pg)
        self.lock_held = False
        return pos

    def _submit(self, ptr: int, discarded: bool, flags: int = 0) -> bool:
        rec = self.records.get(ptr)
        if rec is None or rec.state != "reserved":
            return False
        rec.state = "discarded" if discarded else "committed"
        # 记录指针 + 头里的 pg_off 即可还原 ring buffer 位置（无需额外参数）
        self._put_hdr(ptr, rec.length, DISCARD_BIT if discarded else 0)
        caught_up = self.consumer >= ptr
        if flags & RB_FORCE_WAKEUP or (caught_up and not flags & RB_NO_WAKEUP):
            self.notifications += 1
        return True

    def commit(self, ptr: int, flags: int = 0) -> bool:
        return self._submit(ptr, discarded=False, flags=flags)

    def discard(self, ptr: int, flags: int = 0) -> bool:
        """discard 只是打个标记让消费者跳过 —— 用于 all-or-nothing 提交。"""
        return self._submit(ptr, discarded=True, flags=flags)

    def write_payload(self, ptr: int, payload: bytes) -> None:
        rec = self.records[ptr]
        if len(payload) > rec.length:
            raise ValueError("载荷超出预留长度")
        rec.payload = payload
        start = (ptr + HDR_SIZE) & (self.size - 1)
        for i, b in enumerate(payload):
            self.data[(start + i) & (self.size - 1)] = b

    def output(self, payload: bytes, flags: int = 0) -> bool:
        """`bpf_ringbuf_output()`：先在外面备好数据再拷贝进来（多一次拷贝）。"""
        ptr = self.reserve(len(payload))
        if ptr is None:
            return False
        self.write_payload(ptr, payload)
        return self.commit(ptr, flags)

    # ---------------------------------------------------------------- 消费 ---
    def _ready(self) -> Iterator[Record]:
        """按**保留顺序**遍历可见记录；遇到未提交记录即停（它挡住后面所有）。"""
        pos = self.consumer
        while pos < self.producer:
            rec = self.records[pos]
            if rec.state == "reserved":
                return
            yield rec
            pos += rec.total

    def avail_data(self) -> int:
        """BPF_RB_AVAIL_DATA：已提交且未消费的字节数（只是瞬时快照）。"""
        return sum(r.total for r in self._ready())

    def consume_one(self) -> Optional[Record]:
        for rec in self._ready():
            self.consumer += rec.total
            self.records.pop(rec.pos, None)
            return rec
        return None

    def drain(self) -> List[Record]:
        out = []
        while True:
            r = self.consume_one()
            if r is None:
                return out
            out.append(r)

    def query(self, what: int) -> int:
        if what == RB_AVAIL_DATA:
            return self.avail_data()
        if what == RB_RING_SIZE:
            return self.size
        if what == RB_CONS_POS:
            return self.consumer
        if what == RB_PROD_POS:
            return self.producer
        raise ValueError("未知查询类型")

    # ------------------------------------------------------------ 双映射读 ---
    def read_linear(self, pos: int, n: int) -> bytes:
        """数据区被连续映射两遍 ⇒ 绕回处读出来仍是连续的一段。"""
        off = pos & (self.size - 1)
        return bytes(self.data[off:off + n]) if off + n <= self.size \
            else bytes(self.data[off:]) + bytes(self.data[: n - (self.size - off)])
