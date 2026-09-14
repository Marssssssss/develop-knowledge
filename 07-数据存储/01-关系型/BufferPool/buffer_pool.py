"""
Buffer Pool — InnoDB-style midpoint insertion LRU + clock sweep + dirty
page tracking + checkpoint/fuzzy flush.

Implements:
- Page table (hash: page_id → frame)
- Frame pool with pin/unpin
- Midpoint LRU: new pages enter 5/8 of the way down (i.e. old sublist
  is 3/8 of total = innodb_old_blocks_pct default 37).
- Clock-sweep eviction over the old sublist
- Dirty page tracking + WAL-before-data rule (LSN guard)
- Fuzzy checkpoint marking

References:
- MySQL 8.0 manual §17.5.1 Buffer Pool:
  https://dev.mysql.com/doc/refman/8.0/en/innodb-buffer-pool.html
  "3/8 of the buffer pool is devoted to the old sublist"
- 阿里云 RDS 团队 "InnoDB Buffer Pool flush 策略漫谈":
  https://www.bookstack.cn/read/aliyun-rds-core/f67b42b8f7e9ac29.md
- 庖丁解 InnoDB 之 Buffer Pool (catkang):
  https://catkang.github.io/2023/08/08/mysql-buffer-pool.html
"""

from __future__ import annotations
import hashlib
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Page + frame
# ---------------------------------------------------------------------------
@dataclass
class Page:
    page_id: int
    data: bytes = b""
    lsn: int = 0       # last LSN that modified this page (oldest_modification)


@dataclass
class Frame:
    page: Optional[Page] = None
    pin_count: int = 0
    dirty: bool = False
    # clock-sweep usage counter (cap 5)
    usage: int = 0
    # midpoint insertion: True if in old sublist
    is_old: bool = False


# ---------------------------------------------------------------------------
# Buffer Pool
# ---------------------------------------------------------------------------
class BufferPool:
    """InnoDB-style midpoint LRU + clock sweep + WAL-before-data.

    Parameters:
      size            : frame count (e.g. 100)
      old_blocks_pct  : fraction for old sublist (default 37, i.e. 3/8)
      wal             : callable returning the WAL's current flushed LSN
                        (used to enforce WAL-before-data: only flush a
                        page whose LSN <= WAL flushed LSN)
    """

    def __init__(self, size: int = 100, old_blocks_pct: int = 37,
                 wal=None):
        assert 5 <= old_blocks_pct <= 95
        self.size = size
        self.old_blocks_pct = old_blocks_pct
        self.wal = wal or (lambda: 0)
        self.frames: list[Frame] = [Frame() for _ in range(size)]
        # page_id → frame index
        self.page_table: Dict[int, int] = {}
        # free frames queue (indices)
        self.free = deque(range(size))
        # ordered list of frames in LRU order; new pages inserted at midpoint
        self.lru: list[int] = list(range(size))  # index 0 = MRU head
        # dirty pages in oldest_modification LSN order (Flush List)
        self.flush_list: Dict[int, int] = {}  # page_id → lsn
        # metrics
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.flushes = 0

    # ---- helpers --------------------------------------------------------
    def _mru_index(self) -> int:
        """Index where the old sublist starts (the midpoint)."""
        return self.size * (100 - self.old_blocks_pct) // 100

    def _lru_remove(self, fi: int):
        """Remove fi from the LRU list; maintain fixed size with -1 padding."""
        try:
            self.lru.remove(fi)
            self.lru.append(-1)
        except ValueError:
            pass

    def _lru_insert_old(self, fi: int):
        """Insert at the head of the old sublist (midpoint of full LRU)."""
        mid = self._mru_index()
        self.lru.insert(mid, fi)
        # cap to size: if overflow, drop the last (which is the LRU end)
        if len(self.lru) > self.size:
            del self.lru[-1]
        self.frames[fi].is_old = True

    def _lru_insert_new(self, fi: int):
        """Insert at MRU head."""
        self.lru.insert(0, fi)
        if len(self.lru) > self.size:
            del self.lru[-1]
        self.frames[fi].is_old = False

    # ---- core API -----------------------------------------------------
    def fix(self, page_id: int) -> Frame:
        """FIX a page; pin prevents eviction."""
        if page_id in self.page_table:
            fi = self.page_table[page_id]
            f = self.frames[fi]
            f.pin_count += 1
            # touch: move to MRU head only if it was in old + already pinned
            # Simulates InnoDB "first access from user-initiated op moves
            # to new sublist immediately"
            if f.is_old:
                f.is_old = False
                self._lru_remove(fi)
                self._lru_insert_new(fi)
            self.hits += 1
            return f
        # miss → fetch from "disk"
        self.misses += 1
        fi = self._acquire_frame()
        f = self.frames[fi]
        # evict old mapping
        if f.page is not None:
            self._evict(fi)
        # load new page
        f.page = Page(page_id=page_id, data=f"PAGE[{page_id}]".encode())
        f.dirty = False
        f.usage = 1
        f.pin_count = 1
        self.page_table[page_id] = fi
        # insert at midpoint (old sublist head)
        self._lru_insert_old(fi)
        return f

    def unfix(self, frame: Frame, dirty: bool = False):
        """UNFIX: decrement pin count; mark dirty if modified."""
        assert frame.pin_count >= 1
        frame.pin_count -= 1
        if dirty:
            frame.dirty = True
            # assign LSN = WAL current
            frame.page.lsn = self.wal()
            if frame.page.page_id not in self.flush_list:
                self.flush_list[frame.page.page_id] = frame.page.lsn

    # ---- eviction (clock sweep) --------------------------------------
    def _acquire_frame(self) -> int:
        if self.free:
            return self.free.popleft()
        # walk the LRU tail (old sublist) looking for unpinned, low-usage
        # frame; this is the InnoDB clock-sweep analogue
        old_start = self._mru_index()
        candidates = self.lru[old_start:]
        for fi in reversed(candidates):  # tail first
            f = self.frames[fi]
            if f.pin_count == 0:
                if f.usage > 1:
                    f.usage -= 1
                    continue
                return fi
        # if all old frames pinned, walk whole LRU
        for fi in reversed(self.lru):
            f = self.frames[fi]
            if f.pin_count == 0:
                return fi
        raise RuntimeError("all frames pinned — cannot acquire")

    def _evict(self, fi: int):
        f = self.frames[fi]
        if f.dirty:
            self._flush(f)
        old_pid = f.page.page_id
        self.page_table.pop(old_pid, None)
        if old_pid in self.flush_list:
            del self.flush_list[old_pid]
        self.evictions += 1
        f.page = None
        f.dirty = False
        f.usage = 0
        self._lru_remove(fi)
        self.free.append(fi)

    # ---- flushing (background writer + checkpoint) --------------------
    def _flush(self, f: Frame):
        """WAL-before-data: only flush if WAL has flushed our LSN."""
        if f.page.lsn > self.wal():
            raise RuntimeError(
                f"WAL-before-data violated: page_lsn={f.page.lsn} "
                f"wal_flushed={self.wal()}")
        # write to disk (simulated by no-op)
        f.dirty = False
        self.flush_list.pop(f.page.page_id, None)
        self.flushes += 1

    def background_flush(self, max_pages: int = 4) -> int:
        """InnoDB Page Cleaner: trick out a batch of dirty pages."""
        # flush oldest-modified pages first
        ordered = sorted(self.flush_list.items(), key=lambda kv: kv[1])
        n = 0
        for pid, _lsn in ordered:
            if n >= max_pages:
                break
            if pid in self.page_table:
                fi = self.page_table[pid]
                f = self.frames[fi]
                if f.pin_count == 0 and f.dirty:
                    self._flush(f)
                    n += 1
        return n

    def checkpoint(self):
        """Fuzzy checkpoint: write a marker into WAL, do not stop writes."""
        # mark checkpoint LSN; ARIES-style: "all LSN ≤ ckpt may be discarded"
        ckpt_lsn = self.wal()
        # flush all dirty pages whose LSN <= ckpt_lsn
        flushed = 0
        for pid in list(self.flush_list.keys()):
            if self.flush_list[pid] <= ckpt_lsn:
                if pid in self.page_table:
                    fi = self.page_table[pid]
                    self._flush(self.frames[fi])
                    flushed += 1
        return flushed


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def _run_demo():
    wal_lsn = [0]

    def wal_flush():
        wal_lsn[0] += 10
        return wal_lsn[0]

    pool = BufferPool(size=10, old_blocks_pct=37, wal=wal_flush)

    # 1. sequential scan: pages 1..10 — should NOT evict pages 1..5 hot
    for pid in range(1, 11):
        f = pool.fix(pid)
        pool.unfix(f)

    # 2. hot pages 1..5 — keep accessing them
    for _ in range(3):
        for pid in range(1, 6):
            f = pool.fix(pid)
            pool.unfix(f)

    # 3. write some dirty pages
    f = pool.fix(2)
    f.page.data = b"UPDATED[2]"
    pool.unfix(f, dirty=True)
    f = pool.fix(4)
    f.page.data = b"UPDATED[4]"
    pool.unfix(f, dirty=True)

    print("== after hot loop + 2 dirty writes ==")
    print(f"hits={pool.hits}  misses={pool.misses}  "
          f"evictions={pool.evictions}  flushes={pool.flushes}  "
          f"dirty_pending={len(pool.flush_list)}")
    print("LRU order (MRU→LRU):", [pool.frames[fi].page.page_id
                                    for fi in pool.lru
                                    if pool.frames[fi].page is not None])

    # 4. background writer batch
    flushed = pool.background_flush(max_pages=4)
    print(f"background writer flushed {flushed} pages")

    # 5. mid-loop scan resistance: read pages 100..200, check that pages
    #    1..5 (the hot set) are still resident
    pool.fix(100).pin_count -= 1
    pool.unfix(pool.fix(100))
    for pid in range(100, 150):
        pool.unfix(pool.fix(pid))
    resident = pool.page_table.keys()
    print(f"hot pages 1..5 still resident after scan? "
          f"{all(p in resident for p in range(1, 6))}")


if __name__ == "__main__":
    _run_demo()