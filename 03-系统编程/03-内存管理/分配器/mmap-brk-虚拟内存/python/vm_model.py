"""brk/sbrk 与 mmap —— 进程地址空间接口层的语义模型(共享模块)。

权威来源(Linux man-pages 6.19,实际联网阅读):
  - man7.org/linux/man-pages/man2/brk.2.html
  - man7.org/linux/man-pages/man2/mmap.2.html
  - man7.org/linux/man-pages/man3/malloc.3.html
  - man7.org/linux/man-pages/man3/mallopt.3.html

模型只复现手册**明文规定**的语义;手册未规定者不复现、不断言。
被 main.py 与测试共用,故独立成模块。
"""

PAGE = 4096
HUGE_PAGE = 2 * 1024 * 1024
PAGE_MASK = PAGE - 1

# mmap 返回值与 errno
MAP_FAILED = -1
EINVAL, ENOMEM, EEXIST = 22, 12, 17

PROT_NONE, PROT_READ, PROT_WRITE = 0x0, 0x1, 0x2
MAP_SHARED, MAP_PRIVATE = 0x01, 0x02
MAP_FIXED, MAP_FIXED_NOREPLACE, MAP_ANONYMOUS = 0x10, 0x100000, 0x20

HEAP_START = 0x0000_5555_0000_0000
MMAP_BASE = 0x0000_7F00_0000_0000


class OSError_(Exception):
    def __init__(self, errno, what):
        super().__init__(f"{what}: errno={errno}")
        self.errno = errno


def page_align_up(x):
    return (x + PAGE_MASK) & ~PAGE_MASK


def page_align_down(x):
    return x & ~PAGE_MASK


class AddressSpace:
    """单个进程的地址空间:程序 break + 一组 mmap 区域。"""

    def __init__(self, heap_min=HEAP_START):
        self.heap_min = heap_min
        self.break_ = heap_min
        self.next_hint = MMAP_BASE
        self.maps = []  # 每项: {start,length,flags,offset,data(page 对齐的 bytearray)}
        self.unmapped_spans = []  # 记录被 munmap 掉的地址跨度,便于断言

    # ---------- brk / sbrk ----------

    def sys_brk(self, addr):
        """Linux brk(2) **系统调用**语义:成功返回新 break,失败返回**当前** break。"""
        if addr < self.heap_min or (addr - self.heap_min) > 1 << 40:
            return self.break_
        self.break_ = addr
        return self.break_

    def glibc_brk(self, addr):
        """glibc 包装函数语义:成功 0,失败 -1(errno=ENOMEM)。"""
        r = self.sys_brk(addr)
        if r != addr:
            raise OSError_(ENOMEM, "brk")
        return 0

    def sbrk(self, increment):
        """glibc 库函数:成功返回**旧的** program break;失败返回 (void*)-1。"""
        old = self.break_
        if increment == 0:
            return old
        new = old + increment
        if self.sys_brk(new) != new:
            return MAP_FAILED
        return old

    # ---------- mmap / munmap ----------

    def _overlaps(self, start, length):
        end = start + length
        return [m for m in self.maps if m["start"] < end and start < m["start"] + m["length"]]

    def mmap(self, addr, length, prot, flags, offset=0, huge=False):
        if length <= 0:
            raise OSError_(EINVAL, "mmap: length must be > 0")
        if (flags & MAP_PRIVATE) and (flags & MAP_SHARED):
            raise OSError_(EINVAL, "mmap: exactly one of MAP_PRIVATE/MAP_SHARED")
        if not (flags & (MAP_PRIVATE | MAP_SHARED)):
            raise OSError_(EINVAL, "mmap: exactly one of MAP_PRIVATE/MAP_SHARED")
        unit = HUGE_PAGE if huge else PAGE
        if offset % unit != 0:
            raise OSError_(EINVAL, f"mmap: offset must be a multiple of {unit}")

        if flags & (MAP_FIXED | MAP_FIXED_NOREPLACE):
            if addr is None or addr % unit != 0:
                raise OSError_(EINVAL, "mmap: fixed addr must be suitably aligned")
            start = addr
            if self._overlaps(start, length):
                if flags & MAP_FIXED_NOREPLACE:
                    raise OSError_(EEXIST, "mmap: MAP_FIXED_NOREPLACE hit existing mapping")
                # MAP_FIXED:重叠部分被丢弃
                self._discard(start, length)
        else:
            start = self._pick(addr, length, unit)

        span = page_align_up(length) if not huge else length
        self.maps.append(
            {
                "start": start,
                "length": span,
                "flags": flags,
                "offset": offset,
                "data": bytearray(span),  # MAP_ANONYMOUS:内容初始化为 0
            }
        )
        return start

    def _pick(self, addr, length, unit):
        span = page_align_up(length)
        cand = addr if addr is not None else self.next_hint
        cand = (cand + unit - 1) & ~(unit - 1)
        while self._overlaps(cand, span):
            cand = page_align_up(cand + span)
        self.next_hint = cand + span
        return cand

    def _discard(self, start, length):
        """只丢弃 [start, start+length) 覆盖到的部分;映射的其余部分必须保留。

        手册原文:mmap(2) MAP_FIXED "the overlapped part of the existing
        mapping(s) will be discarded";munmap(2) 同理只卸载范围内的页。
        """
        lo, hi = start, start + length
        for m in list(self.maps):
            m_lo, m_hi = m["start"], m["start"] + m["length"]
            if m_lo >= hi or lo >= m_hi:
                continue
            self.unmapped_spans.append((max(lo, m_lo), min(hi, m_hi)))
            self.maps.remove(m)
            if m_lo < lo:  # 保留左段
                keep = lo - m_lo
                self.maps.append({**m, "length": keep, "data": m["data"][:keep]})
            if hi < m_hi:  # 保留右段
                off = hi - m_lo
                self.maps.append({**m, "start": hi, "length": m_hi - hi, "data": m["data"][off:]})

    def munmap(self, addr, length):
        if addr % PAGE != 0:
            raise OSError_(EINVAL, "munmap: addr must be a multiple of the page size")
        # length 不必是页的整数倍;覆盖到的**整页**都会被卸载
        lo, hi = addr, page_align_up(addr + length)
        self._discard(lo, hi - lo)
        return 0

    # ---------- 观测 ----------

    def find(self, addr):
        for m in self.maps:
            if m["start"] <= addr < m["start"] + m["length"]:
                return m
        return None

    def page_of(self, addr):
        return self.find(page_align_down(addr))

    def store(self, addr, nbytes, value=0xA5):
        m = self.page_of(addr)
        if m is None:
            raise OSError_(EINVAL, "store: address not mapped")
        off = addr - m["start"]
        for i in range(nbytes):
            m["data"][off + i] = value

    def load(self, addr, nbytes):
        m = self.page_of(addr)
        if m is None:
            raise OSError_(EINVAL, "load: address not mapped")
        off = addr - m["start"]
        return bytes(m["data"][off : off + nbytes])

    # ---------- malloc 的 threshold 策略 (mallopt(3)) ----------

    def brk_managed(self):
        """由 program break 直接管理、尚未归还的堆尾字节数。"""
        return self.break_ - self.heap_min


DEFAULT_MMAP_THRESHOLD = 128 * 1024
DEFAULT_MMAP_THRESHOLD_MAX = 4 * 1024 * 1024 * 8  # 64 位:4*1024*1024*sizeof(long)
DEFAULT_TRIM_THRESHOLD = 128 * 1024
DEFAULT_TOP_PAD = 128 * 1024
DEFAULT_MMAP_MAX = 65536


class ThresholdPolicy:
    """mallopt(3) 描述的**动态 mmap 阈值**策略。"""

    def __init__(self):
        self.mmap_threshold = DEFAULT_MMAP_THRESHOLD
        self.trim_threshold = DEFAULT_TRIM_THRESHOLD  # 动态生效时 = 2 * mmap_threshold
        self.mmap_max = DEFAULT_MMAP_MAX
        self.dynamic = True

    def classify(self, nbytes):
        """>= 阈值且无法从空闲链表满足时走 mmap(2),否则走堆(sbrk)。"""
        return "mmap" if nbytes >= self.mmap_threshold else "heap"

    def on_free(self, block_size):
        """释放一个块后按 mallopt(3) 原文调整阈值;返回调整动作名。"""
        if not self.dynamic:
            return "frozen"
        if block_size > self.mmap_threshold and block_size <= DEFAULT_MMAP_THRESHOLD_MAX:
            self.mmap_threshold = block_size
            self.trim_threshold = 2 * self.mmap_threshold
            return "raise"
        return "keep"

    def mallopt(self, param):
        """一旦显式设置 M_TRIM_THRESHOLD/M_TOP_PAD/M_MMAP_THRESHOLD/M_MMAP_MAX -> 关闭动态调整。"""
        if param in ("M_TRIM_THRESHOLD", "M_TOP_PAD", "M_MMAP_THRESHOLD", "M_MMAP_MAX"):
            self.dynamic = False

