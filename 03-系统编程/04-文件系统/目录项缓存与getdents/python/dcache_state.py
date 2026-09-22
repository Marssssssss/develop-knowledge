"""Dentry 与 DCache 的状态模型（从 dcache.py 拆出）：引用计数、LRU、negative dentry。

四种状态：in-use（ref>0，不在 LRU）/ unused（ref==0，在 LRU）/
negative（d_inode 为 NULL，类型 MISS）/ killed（已摘哈希，等 RCU 释放）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dcache import (  # noqa: E402
    DCACHE_DENTRY_KILLED, DCACHE_ENTRY_TYPE, DCACHE_MISS_TYPE,
    DCACHE_REFERENCED,
)

class Dentry:
    """struct dentry 的模型：只保留 dcache 语义相关的字段。

    refcount 是 d_lockref.count；0 表示"未使用（unused）"，可以被回收；
    负数（dentry 被杀）在本模型里用 killed 表示。
    """

    __slots__ = ("name", "flags", "refcount", "inode", "parent", "children",
                 "lru", "killed")

    def __init__(self, name, flags=DCACHE_MISS_TYPE, inode=None, parent=None):
        self.name = name
        self.flags = flags
        self.refcount = 1
        self.inode = inode
        self.parent = parent
        self.children = []
        self.lru = False
        self.killed = False

    def set_type(self, t):
        self.flags = (self.flags & ~DCACHE_ENTRY_TYPE) | t
        return self

    def __repr__(self):
        return "Dentry(%r, type=%s, ref=%d)" % (
            self.name, TYPE_NAME.get(dentry_type(self.flags), "?"), self.refcount)


class DCache:
    """哈希 + LRU 的最小模型。

    内核里 dentry 有四种状态：in-use（refcount>0，挂在父目录的 d_children）、
    unused（refcount==0，仍在哈希里，挂在 LRU）、negative（d_inode==NULL）、
    dying/killed（已从哈希摘除，等待 RCU 释放）。
    """

    def __init__(self):
        self.table = {}          # (parent, name) -> Dentry
        self.lru = []            # 未使用 dentry 的 LRU 顺序（尾部最近使用）

    def lookup(self, parent, name):
        return self.table.get((id(parent), name))

    def insert(self, parent, name, dentry):
        dentry.parent = parent
        self.table[(id(parent), name)] = dentry
        if parent is not None:
            parent.children.append(dentry)
        return dentry

    def dget(self, dentry):
        dentry.refcount += 1
        if dentry.refcount == 1 and dentry in self.lru:
            self.lru.remove(dentry)
            dentry.lru = False
        return dentry

    def dput(self, dentry):
        dentry.refcount -= 1
        if dentry.refcount == 0 and not dentry.lru and not dentry.killed:
            self.lru.append(dentry)
            dentry.lru = True
        return dentry.refcount

    def touch_lru(self, dentry):
        """dentry 被访问：置 DCACHE_REFERENCED 并移到 LRU 尾部。"""
        dentry.flags |= DCACHE_REFERENCED
        if dentry in self.lru:
            self.lru.remove(dentry)
            self.lru.append(dentry)

    def shrink_one(self):
        """收缩一个未使用 dentry。返回被回收的名字。

        有 DCACHE_REFERENCED 的先**清标志再给第二次机会**（内核
        shrink_dentry_list 的两轮策略），而不是直接回收。
        """
        while self.lru:
            d = self.lru.pop(0)
            if d.refcount != 0:
                continue
            if d.flags & DCACHE_REFERENCED:
                d.flags &= ~DCACHE_REFERENCED
                self.lru.append(d)
                continue
            d.killed = True
            d.lru = False
            d.flags |= DCACHE_DENTRY_KILLED
            key = (id(d.parent), d.name)
            if self.table.get(key) is d:
                del self.table[key]
            return d.name
        return None

    def negative_lookup(self, parent, name):
        """查不到的路径也建 dentry——negative dentry，避免反复下探文件系统。"""
        d = Dentry(name, DCACHE_MISS_TYPE, inode=None, parent=parent)
        return self.insert(parent, name, d)


# --------------------------------------------------------------------------
# getdents64
# --------------------------------------------------------------------------
