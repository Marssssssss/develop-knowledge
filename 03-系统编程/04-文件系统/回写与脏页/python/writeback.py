"""Linux 脏页回写与 vm.dirty_* 调优的可执行模型。

事实来源：docs.kernel.org《Documentation for /proc/sys/vm/》全文相关条目（实读）。

要点（均为文档原文归纳）：
  * dirty_background_ratio / dirty_ratio 的分母是 **total available memory**
    （含 free pages 与 reclaimable pages），**不等于系统总内存**
  * *_bytes 与 *_ratio 互为 counterpart：**任一时刻只能有一个生效**，
    写一个之后另一个**读出来是 0**
  * dirty_bytes 的最小允许值是**两个页**，低于它会被忽略、保留旧配置
  * dirty_expire_centisecs / dirty_writeback_centisecs 的单位是 **1/100 秒**
  * dirty_writeback_centisecs = 0 → **完全禁用定期回写**
  * dirtytime_expire_seconds = 0 → 禁用定期 dirtytime 回写
  * drop_caches 只丢**干净的**缓存与可回收 slab（dentry/inode）
"""

PAGE = 4096


class VmSysctl(object):
    """/proc/sys/vm/ 里与回写相关的几个旋钮。"""

    def __init__(self):
        self.dirty_background_ratio = 10
        self.dirty_background_bytes = 0
        self.dirty_ratio = 20
        self.dirty_bytes = 0
        self.dirty_expire_centisecs = 3000      # 30 秒
        self.dirty_writeback_centisecs = 500    # 5 秒
        self.dirtytime_expire_seconds = 43200   # 12 小时

    def write(self, name, value):
        """写一个 sysctl，并处理 counterpart 互斥与最小值约束。"""
        if name == "dirty_bytes":
            if value != 0 and value < 2 * PAGE:
                return False        # 文档：低于两页直接忽略，保留旧配置
            self.dirty_bytes = value
            self.dirty_ratio = 0    # counterpart 读出来是 0
            return True
        if name == "dirty_ratio":
            self.dirty_ratio = value
            self.dirty_bytes = 0
            return True
        if name == "dirty_background_bytes":
            if value != 0 and value < 2 * PAGE:
                return False
            self.dirty_background_bytes = value
            self.dirty_background_ratio = 0
            return True
        if name == "dirty_background_ratio":
            self.dirty_background_ratio = value
            self.dirty_background_bytes = 0
            return True
        if name in ("dirty_expire_centisecs", "dirty_writeback_centisecs",
                    "dirtytime_expire_seconds"):
            setattr(self, name, value)
            return True
        raise KeyError(name)

    def periodic_writeback_enabled(self):
        return self.dirty_writeback_centisecs != 0

    def dirtytime_writeback_enabled(self):
        return self.dirtytime_expire_seconds != 0


class Machine(object):
    """一台内存可记账的机器。dirty 是一串 (字节数, 已脏了多少 centisecs)。"""

    def __init__(self, total_mb=8192, free_mb=6144, reclaimable_mb=1024,
                 vm=None):
        self.total_pages = total_mb * 1024 * 1024 // PAGE
        self.free_pages = free_mb * 1024 * 1024 // PAGE
        self.reclaimable_pages = reclaimable_mb * 1024 * 1024 // PAGE
        self.vm = vm or VmSysctl()
        self.dirty = []          # [(bytes, age_cs), ...]
        self.written = 0         # 已回写字节（统计用）
        self.blocked = 0         # 写者被限流的次数
        self.elapsed_cs = 0
        self._next_wakeup = self.vm.dirty_writeback_centisecs

    # ---- 内存口径 ----
    def available_bytes(self):
        """文档：free pages + reclaimable pages，**不是**系统总内存。"""
        return (self.free_pages + self.reclaimable_pages) * PAGE

    def total_bytes(self):
        return self.total_pages * PAGE

    def dirty_bytes(self):
        return sum(b for b, _ in self.dirty)

    # ---- 阈值 ----
    def background_thresh(self):
        v = self.vm
        if v.dirty_background_bytes:
            return v.dirty_background_bytes
        return self.available_bytes() * v.dirty_background_ratio // 100

    def dirty_thresh(self):
        v = self.vm
        if v.dirty_bytes:
            return v.dirty_bytes
        return self.available_bytes() * v.dirty_ratio // 100

    # ---- 动作 ----
    def write(self, nbytes):
        """进程写数据：弄脏 nbytes；越过 dirty 阈值时**写者自己**开始回写。"""
        self.dirty.append([nbytes, 0])
        if self.dirty_bytes() >= self.dirty_thresh():
            self.blocked += 1
            self._flush_until_below(self.dirty_thresh())
        return self.blocked

    def _flush_until_below(self, thresh):
        """按最老优先回写，直到低于阈值。"""
        self.dirty.sort(key=lambda x: -x[1])
        while self.dirty and self.dirty_bytes() >= thresh:
            chunk, _ = self.dirty.pop()
            self.written += chunk
            self.free_pages += chunk // PAGE

    def tick(self, dt_cs):
        """推进 dt 个 centisecs；到点唤醒 flusher。"""
        self.elapsed_cs += dt_cs
        for d in self.dirty:
            d[1] += dt_cs
        if not self.vm.periodic_writeback_enabled():
            return []
        if self.elapsed_cs < self._next_wakeup:
            return []
        self._next_wakeup = self.elapsed_cs + self.vm.dirty_writeback_centisecs
        return self.flusher_wakeup()

    def flusher_wakeup(self):
        """flusher 线程醒来：先写"过期"的，再判断是否因超后台阈值而继续写。"""
        flushed = []
        # 1) 脏了超过 dirty_expire_centisecs 的数据这一轮必须写
        keep = []
        for chunk, age in self.dirty:
            if age >= self.vm.dirty_expire_centisecs:
                flushed.append(chunk)
                self.written += chunk
                self.free_pages += chunk // PAGE
            else:
                keep.append([chunk, age])
        self.dirty = keep
        # 2) 超过后台阈值 → 继续写到低于阈值为止
        if self.dirty_bytes() >= self.background_thresh():
            before = self.dirty_bytes()
            self._flush_until_below(self.background_thresh())
            flushed.append(before - self.dirty_bytes())
        return flushed

    def drop_caches(self):
        """drop_caches：只回收干净的页缓存与可回收 slab，脏页不受影响。"""
        before = self.free_pages
        self.free_pages += self.reclaimable_pages
        self.reclaimable_pages = 0
        return (self.free_pages - before) * PAGE
