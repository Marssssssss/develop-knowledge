"""演示入口：dcache 与 getdents64。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dcache import (  # noqa: E402
    DCACHE_DIRECTORY_TYPE, DCACHE_ENTRY_TYPE, DCACHE_MISS_TYPE,
    DCACHE_REGULAR_TYPE, DCACHE_TO_DT, DT_NAME, DT_REG,
    TYPE_NAME,
    dentry_type,
)
from dcache_state import DCache, Dentry  # noqa: E402
from dirent64 import (  # noqa: E402
    DIRENT64_HEADER, EINVAL, dirent64_size, emit_getdents, walk,
)


def hdr(t):
    print()
    print("== %s ==" % t)


hdr("d_flags 的类型字段：3 位（19..21），不是独立标志位")
print("  DCACHE_ENTRY_TYPE = 0x%X" % DCACHE_ENTRY_TYPE)
for t in sorted(TYPE_NAME):
    print("  %-16s → 值 %d，映射到 d_type %s"
          % (TYPE_NAME[t], t >> 19, DT_NAME[DCACHE_TO_DT[t]]))
print("  >>> MISS 是 0，所以『清掉类型位』就等于变成 negative；")
print("  >>> 七个类型值两两不同，与 bit18/22 的标志位互不干扰。")

hdr("dcache 生命周期")
c = DCache()
root = Dentry("/", DCACHE_DIRECTORY_TYPE)
d = c.insert(root, "f", Dentry("f", DCACHE_REGULAR_TYPE, inode=1))
print("  d_alloc       : ref=%d lru=%s" % (d.refcount, d in c.lru))
c.dput(d)
print("  dput 归零     : ref=%d lru=%s（unused，可被回收）"
      % (d.refcount, d in c.lru))
c.dget(d)
print("  dget          : ref=%d lru=%s（in-use，不可回收）"
      % (d.refcount, d in c.lru))

neg = c.negative_lookup(root, "missing")
print("  negative 查询 : type=%s d_inode=%s 仍在哈希=%s"
      % (TYPE_NAME[dentry_type(neg.flags)], neg.inode,
         c.lookup(root, "missing") is neg))
print("  >>> negative dentry 缓存的是『这个文件不存在』，避免反复下探文件系统")

c2 = DCache()
r2 = Dentry("/", DCACHE_DIRECTORY_TYPE)
for n in ("x", "y", "z"):
    c2.dput(c2.insert(r2, n, Dentry(n, DCACHE_REGULAR_TYPE, inode=1)))
c2.touch_lru(c2.lru[0])
print("  收缩顺序      : %s（被 touch 的排最后，且先清 REFERENCED 再回收）"
      % [c2.shrink_one() for _ in range(3)])

hdr("struct linux_dirent64（头部 19 字节，8 字节对齐）")
print("  d_ino(8) d_off(8) d_reclen(2) d_type(1) d_name[]")
for name in ("a", "abc", "abcdef", "hello.txt", "n" * 255):
    raw = DIRENT64_HEADER + len(name) + 1
    print("  name=%-12s 原始 %-4d → d_reclen %-4d（填充 %d）"
          % (name[:12], raw, dirent64_size(name),
             dirent64_size(name) - raw))

hdr("getdents64：装填与 EINVAL 的时机")
entries = [(1, "one", DT_REG), (2, "two", 4), (3, "three", 10)]
for bs in (4096, dirent64_size("one"), dirent64_size("one") - 1, 0):
    buf, n, rest, ret = emit_getdents(entries, bs)
    print("  buffer=%-5d → 写入 %d 条，返回 %s"
          % (bs, n, "EINVAL" if ret == EINVAL else "%d 字节" % ret))
print("  >>> 只要发出过至少一条，内核就用『写入字节数』覆盖预设的 EINVAL；")
print("  >>> EINVAL 只在一条都装不下时才可见（man page: Result buffer is too small）")

buf, n, rest, ret = emit_getdents(entries, 4096)
print("  最后一条的 d_off 被覆写成 ctx.pos = %d（续读位置，不是偏移）"
      % walk(buf)[-1][1])

hdr("大目录：多次 getdents 拼接")
many = [(i, "f%05d" % i, DT_REG) for i in range(5000)]
got, pos, calls = [], 0, 0
while pos < len(many):
    buf, n, rest, ret = emit_getdents(many[pos:], 4096)
    if ret == EINVAL:
        break
    got.extend(r[4] for r in walk(buf))
    pos += n
    calls += 1
print("  5000 个条目 / 4KiB 缓冲 → %d 次调用，收齐 %d 条，顺序一致=%s"
      % (calls, len(got), got == ["f%05d" % i for i in range(5000)]))
