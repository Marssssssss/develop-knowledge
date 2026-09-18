#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Redis 字典的「双表 + 渐进式 rehash」复刻。

口径来源（先联网实读再写）：Redis 源码 unstable 分支 src/dict.c
（raw.githubusercontent.com/redis/redis/unstable/src/dict.c，经 WebFetch 全文读取）。逐条对应：
  - `struct dict` 用两张表：`d->ht_table[0/1]`、`d->ht_size_exp[0/1]`、`d->ht_used[0/1]`，
    外加 `d->rehashidx`（-1 表示没有在 rehash）、`d->pauserehash`；
  - `_dictResize()` 注释 "Prepare a second hash table for incremental rehashing."；
    若 ht[0] 为空则直接把新表放进 ht[0] 并 `_dictReset(d,1)`、`rehashidx = -1`；
  - `_dictNextExp()`："Our hash table capability is a power of two"；
  - `dictExpandIfNeeded()`：已达 **1:1** 且允许 resize 时扩容；`dict_can_resize != DICT_RESIZE_FORBID`
    时达到 `dict_force_resize_ratio`（源码内定义 **= 4**）也扩容；
  - `dictShrinkIfNeeded()`：低于 **1:8**（`HASHTABLE_MIN_FILL`）或低于 **1:32**
    （`HASHTABLE_MIN_FILL * dict_force_resize_ratio`）时收缩；
  - `dictRehash()` 注释：一步 = 搬一个桶；"it will visit at max **N*10 empty buckets** in total,
    otherwise the amount of work it does would be unbound"；
  - `dictCheckRehashingCompleted()`：`ht_used[0] == 0` 时把 ht[1] 拷回 ht[0]、`rehashidx = -1`；
  - `_dictRehashStep()`：由查找/更新操作触发，让字典「在被使用的同时」把键从 H1 搬到 H2；
  - 查找遍历两张表并 `if (table == 0 && (long)idx < d->rehashidx) continue;` 跳过已迁移区间；
  - `dictFindLinkForInsert()`："the bucket is always returned in the context of the **second (new)**
    hash table"，即 rehash 期间插入进 ht[1]；
  - `dictNext()`：安全迭代器会 `dictPauseRehashing()`。
  - 备注：`DICT_HT_INITIAL_SIZE` / `DICT_HT_INITIAL_EXP` / `HASHTABLE_MIN_FILL` 的数值定义在
    dict.h（本次未读取），本 demo 用 INITIAL_SIZE=4、MIN_FILL=8 复现 1:8 / 1:32 的语义并显式标注。

运行：python3 dict_rehash.py —— 打印演示输出；断言见 checks.py
"""
from __future__ import annotations

import math

# ---- 常量：dict_force_resize_ratio 来自 dict.c；其余见文件头备注 ----
INITIAL_SIZE = 4          # DICT_HT_INITIAL_SIZE（dict.h 未读，取 4 演示）
MIN_FILL = 8              # HASHTABLE_MIN_FILL（由 1:8 语义反推）
FORCE_RESIZE_RATIO = 4    # dict.c: static const unsigned int dict_force_resize_ratio = 4
ENABLE, AVOID, FORBID = "enable", "avoid", "forbid"


def next_exp(size: int) -> int:
    """对应 _dictNextExp()：容量永远是 2 的幂。"""
    if size <= INITIAL_SIZE:
        return int(math.log2(INITIAL_SIZE))
    return (size - 1).bit_length()


class Dict:
    """双表字典。桶实现为「链表」的 Python 等价物（list of (key, value)）。"""

    def __init__(self, resize_mode: str = ENABLE):
        self.ht: list[list[list[tuple[str, str]]]] = [[], []]
        self.size_exp = [-1, -1]
        self.used = [0, 0]
        self.rehashidx = -1
        self.pauserehash = 0
        self.resize_mode = resize_mode
        self.rehash_steps = 0          # 累计搬过的桶数（统计用）
        self.resize_log: list[tuple[int, int]] = []   # (旧容量, 新容量)，用于断言 resize 时机

    # ---------- 基础量 ----------
    def size(self, idx: int) -> int:
        return 0 if self.size_exp[idx] < 0 else 1 << self.size_exp[idx]

    def is_rehashing(self) -> bool:
        return self.rehashidx != -1

    def total(self) -> int:
        return self.used[0] + self.used[1]

    def _bucket_index(self, key: str, idx: int) -> int:
        return hash(key) & (self.size(idx) - 1)

    # ---------- resize / expand / shrink ----------
    def _resize(self, size: int) -> bool:
        """对应 _dictResize()。返回 True 表示真的换了表。"""
        if self.is_rehashing():
            return False                          # "We can't rehash twice if rehashing is ongoing."
        new_exp = next_exp(size)
        if new_exp == self.size_exp[0]:
            return False                          # "Rehashing to the same table size is not useful."
        self.ht[1] = [[] for _ in range(1 << new_exp)]
        self.resize_log.append((self.size(0), 1 << new_exp))
        self.size_exp[1] = new_exp
        self.used[1] = 0
        self.rehashidx = 0                        # 第二张表就绪，开始渐进式 rehash
        if self.size_exp[0] < 0 or self.used[0] == 0:
            self._swap_and_finish()                # 首次初始化：不是真 rehash，直接换过去
        return True

    def expand(self, size: int) -> bool:
        if self.is_rehashing() or self.used[0] > size or self.size(0) >= size:
            return False
        return self._resize(size)

    def shrink(self, size: int) -> bool:
        if self.is_rehashing() or self.used[0] > size or self.size(0) <= size:
            return False
        return self._resize(size)

    def expand_if_needed(self) -> str:
        """对应 dictExpandIfNeeded()：1:1 扩容；禁止 resize 时仍在 4 倍处强制扩容。"""
        if self.is_rehashing():
            return "rehashing"
        if self.size(0) == 0:
            self.expand(INITIAL_SIZE)
            return "init"
        if self.resize_mode == ENABLE and self.used[0] >= self.size(0):
            self.expand(self.used[0] + 1)
            return "load_factor_1:1"
        if self.resize_mode != FORBID and self.used[0] >= FORCE_RESIZE_RATIO * self.size(0):
            self.expand(self.used[0] + 1)
            return "force_ratio_4x"
        return "none"

    def shrink_if_needed(self) -> str:
        """对应 dictShrinkIfNeeded()：1:8 收缩；AVOID 时退到 1:32。"""
        if self.is_rehashing() or self.size(0) <= INITIAL_SIZE:
            return "none"
        if self.resize_mode == ENABLE and self.used[0] * MIN_FILL <= self.size(0):
            self.shrink(self.used[0])
            return "fill_below_1:8"
        if (self.resize_mode != FORBID
                and self.used[0] * MIN_FILL * FORCE_RESIZE_RATIO <= self.size(0)):
            self.shrink(self.used[0])
            return "fill_below_1:32"
        return "none"

    # ---------- 渐进式 rehash ----------
    def rehash(self, n: int) -> bool:
        """对应 dictRehash()：搬最多 n 个非空桶，最多探望 n*10 个空桶。

        返回 True 表示还有键没搬完。
        """
        if not self.is_rehashing() or self.resize_mode == FORBID:
            return False
        empty_visits = n * 10
        while n > 0 and self.used[0] != 0:
            while self.rehashidx < self.size(0) and not self.ht[0][self.rehashidx]:  # 跳过空桶
                self.rehashidx += 1
                empty_visits -= 1
                if empty_visits == 0:
                    self.rehash_steps += 1
                    return True
            if self.rehashidx >= self.size(0):
                break
            bucket = self.ht[0][self.rehashidx]
            for key, value in bucket:
                target = self._bucket_index(key, 1)
                self.ht[1][target].append((key, value))
                self.used[0] -= 1
                self.used[1] += 1
            self.ht[0][self.rehashidx] = []
            self.rehashidx += 1
            self.rehash_steps += 1
            n -= 1
        return not self._check_completed()

    def _check_completed(self) -> bool:
        """对应 dictCheckRehashingCompleted()。"""
        if self.used[0] != 0:
            return False
        self._swap_and_finish()
        return True

    def _swap_and_finish(self) -> None:
        self.ht[0] = self.ht[1]
        self.size_exp[0] = self.size_exp[1]
        self.used[0] = self.used[1]
        self.ht[1] = []
        self.size_exp[1] = -1
        self.used[1] = 0
        self.rehashidx = -1

    def _rehash_step(self) -> None:
        """对应 _dictRehashStep()：查找/更新顺带搬一个桶。"""
        if self.pauserehash == 0:
            self.rehash(1)

    # ---------- 增删查 ----------
    def find(self, key: str) -> str | None:
        if self.total() == 0:                         # 对应 dictFind 里的 dictSize(d) == 0 早退
            return None
        idx0 = self._bucket_index(key, 0) if self.size(0) else 0
        self._rehash_step_if_needed(idx0)
        tables = 2 if self.is_rehashing() else 1
        for table in range(tables):
            idx = idx0 if self.size(0) == 0 else self._bucket_index(key, table)
            if table == 0 and idx < self.rehashidx:
                continue                              # 该桶已经搬到新表了
            for k, v in self.ht[table][idx]:
                if k == key:
                    return v
        return None

    def _rehash_step_if_needed(self, visited_idx: int) -> None:
        """对应 _dictRehashStepIfNeeded()：优先搬「本次访问到的那个桶」（缓存友好）。"""
        if not self.is_rehashing() or self.pauserehash != 0:
            return
        if visited_idx >= self.rehashidx and self.ht[0][visited_idx]:
            self._bucket_rehash(visited_idx)
        else:
            self._rehash_step()

    def _bucket_rehash(self, idx: int) -> int:
        if not self.is_rehashing() or self.pauserehash != 0:
            return 0
        for key, value in self.ht[0][idx]:
            self.ht[1][self._bucket_index(key, 1)].append((key, value))
            self.used[0] -= 1
            self.used[1] += 1
        self.ht[0][idx] = []
        self.rehash_steps += 1
        self._check_completed()
        return 1

    def insert(self, key: str, value: str) -> bool:
        """返回 True 表示新插入（同键覆盖则返回 False）。"""
        self.expand_if_needed()
        idx0 = self._bucket_index(key, 0) if self.size(0) else 0
        self._rehash_step_if_needed(idx0)
        for table in range(2 if self.is_rehashing() else 1):
            idx = self._bucket_index(key, table)
            if table == 0 and idx < self.rehashidx:
                continue
            for i, (k, _) in enumerate(self.ht[table][idx]):
                if k == key:
                    self.ht[table][idx][i] = (key, value)
                    return False
        target = 1 if self.is_rehashing() else 0       # rehash 期间一律插进新表
        if self.size(target) == 0:
            return False
        self.ht[target][self._bucket_index(key, target)].append((key, value))
        self.used[target] += 1
        return True

    def delete(self, key: str) -> bool:
        self._rehash_step_if_needed(self._bucket_index(key, 0) if self.size(0) else 0)
        for table in range(2 if self.is_rehashing() else 1):
            idx = self._bucket_index(key, table)
            if table == 0 and idx < self.rehashidx:
                continue
            bucket = self.ht[table][idx]
            for i, (k, _) in enumerate(bucket):
                if k == key:
                    bucket.pop(i)
                    self.used[table] -= 1
                    self.shrink_if_needed()
                    return True
        return False

    # ---------- 迭代（dictNext 语义）----------
    def keys_safe(self) -> list[str]:
        """安全迭代器：dictPauseRehashing() 期间不搬桶，遍历 table0 未迁移区 + table1。"""
        self.pauserehash += 1
        out: list[str] = []
        try:
            if self.is_rehashing():
                for idx in range(self.rehashidx, self.size(0)):
                    out.extend(k for k, _ in self.ht[0][idx])
                for idx in range(self.size(1)):
                    out.extend(k for k, _ in self.ht[1][idx])
            else:
                for idx in range(self.size(0)):
                    out.extend(k for k, _ in self.ht[0][idx])
        finally:
            self.pauserehash -= 1
        return out

    def stats(self) -> dict:
        return {"resizes": list(self.resize_log), "size0": self.size(0), "size1": self.size(1),
                "used0": self.used[0], "used1": self.used[1],
                "rehashidx": self.rehashidx, "steps": self.rehash_steps}


if __name__ == "__main__":
    d = Dict()
    for i in range(6):
        d.insert(f"k{i}", f"v{i}")
    print("插入 6 个键后:", d.stats())
    d.rehash(100)
    print("rehash 完成后:", d.stats(), "k5 =", d.find("k5"))
