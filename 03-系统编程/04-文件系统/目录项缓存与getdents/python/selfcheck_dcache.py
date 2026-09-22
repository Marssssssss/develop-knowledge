"""dcache + getdents64 自检：把头文件与 man page 的条文变成断言。"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dcache import (  # noqa: E402
    DCACHE_AUTODIR_TYPE, DCACHE_CANT_MOUNT, DCACHE_DENTRY_KILLED,
    DCACHE_DIRECTORY_TYPE, DCACHE_DISCONNECTED, DCACHE_DONTCACHE,
    DCACHE_ENTRY_TYPE, DCACHE_LRU_LIST, DCACHE_MANAGED_DENTRY,
    DCACHE_MISS_TYPE, DCACHE_MOUNTED, DCACHE_NEED_AUTOMOUNT,
    DCACHE_MANAGE_TRANSIT, DCACHE_NOKEY_NAME, DCACHE_OP_REAL,
    DCACHE_PAR_LOOKUP, DCACHE_PERSISTENT, DCACHE_REFERENCED,
    DCACHE_REGULAR_TYPE, DCACHE_SHRINK_LIST, DCACHE_SPECIAL_TYPE,
    DCACHE_SYMLINK_TYPE, DCACHE_WHITEOUT_TYPE, DCACHE_TO_DT,
    DT_BLK, DT_CHR,
    DT_DIR, DT_FIFO, DT_LNK, DT_REG, DT_SOCK, DT_UNKNOWN, DT_WHT, TYPE_NAME,
    d_can_lookup, d_is_autodir, d_is_directory, d_is_file, d_is_miss,
    d_is_negative, d_is_positive, d_is_reg, d_is_special, d_is_symlink,
    d_is_whiteout, dentry_type, is_managed,
)
from dcache_state import DCache, Dentry  # noqa: E402
from dirent64 import (  # noqa: E402
    DIRENT64_FMT, DIRENT64_HEADER, EINVAL, dirent64_record, dirent64_size,
    emit_getdents, unpack_dirent64, walk,
)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %-56s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-56s %s" % (label, detail))


print("== 1. d_flags 位（include/linux/dcache.h） ==")
check("DCACHE_DISCONNECTED 是 bit5", DCACHE_DISCONNECTED == 1 << 5)
check("DCACHE_REFERENCED 是 bit6", DCACHE_REFERENCED == 1 << 6)
check("DCACHE_DONTCACHE 是 bit7", DCACHE_DONTCACHE == 1 << 7)
check("DCACHE_SHRINK_LIST 是 bit10", DCACHE_SHRINK_LIST == 1 << 10)
check("DCACHE_MOUNTED 是 bit15", DCACHE_MOUNTED == 1 << 15)
check("DCACHE_NEED_AUTOMOUNT 是 bit16", DCACHE_NEED_AUTOMOUNT == 1 << 16)
check("DCACHE_MANAGE_TRANSIT 是 bit17", DCACHE_MANAGE_TRANSIT == 1 << 17)
check("DCACHE_LRU_LIST 是 bit18", DCACHE_LRU_LIST == 1 << 18)
check("DCACHE_NOKEY_NAME 是 bit22", DCACHE_NOKEY_NAME == 1 << 22)
check("DCACHE_OP_REAL 是 bit23", DCACHE_OP_REAL == 1 << 23)
check("DCACHE_PAR_LOOKUP 是 bit24", DCACHE_PAR_LOOKUP == 1 << 24)
check("DCACHE_PERSISTENT 是 bit27", DCACHE_PERSISTENT == 1 << 27)
check("DCACHE_MANAGED_DENTRY = MOUNTED|NEED_AUTOMOUNT|MANAGE_TRANSIT",
      DCACHE_MANAGED_DENTRY == (1 << 15) | (1 << 16) | (1 << 17))
check("managed 掩码不含 LRU_LIST / CANT_MOUNT",
      not (DCACHE_MANAGED_DENTRY & (DCACHE_LRU_LIST | DCACHE_CANT_MOUNT)))

print("== 2. 类型字段是 3 位（19..21），不是独立标志位 ==")
check("DCACHE_ENTRY_TYPE == 7<<19", DCACHE_ENTRY_TYPE == 7 << 19)
check("七个类型值互不相同",
      len({DCACHE_MISS_TYPE, DCACHE_WHITEOUT_TYPE, DCACHE_DIRECTORY_TYPE,
           DCACHE_AUTODIR_TYPE, DCACHE_REGULAR_TYPE, DCACHE_SPECIAL_TYPE,
           DCACHE_SYMLINK_TYPE}) == 7)
check("MISS 是 0（所以『清掉类型位』== negative）", DCACHE_MISS_TYPE == 0)
check("SYMLINK 是 6，是最大值（3 位刚好装下 0..6）",
      DCACHE_SYMLINK_TYPE >> 19 == 6)
check("类型字段与 bit18/22 不重叠",
      not (DCACHE_ENTRY_TYPE & (DCACHE_LRU_LIST | DCACHE_NOKEY_NAME)))
# 类型位与标志位可以共存：同一个 d_flags 上互不干扰
mixed = DCACHE_MISS_TYPE | DCACHE_MOUNTED | DCACHE_LRU_LIST
check("类型位与标志位共存时类型仍读得出来", dentry_type(mixed) == DCACHE_MISS_TYPE)
check("共存时 MOUNTED 也还在", bool(mixed & DCACHE_MOUNTED))
check("set_type 只动类型位",
      Dentry("x", DCACHE_MISS_TYPE | DCACHE_MOUNTED)
      .set_type(DCACHE_REGULAR_TYPE).flags ==
      (DCACHE_REGULAR_TYPE | DCACHE_MOUNTED))

print("== 3. d_is_* 判定（成对构造） ==")
check("MISS → negative", d_is_negative(DCACHE_MISS_TYPE) and d_is_miss(DCACHE_MISS_TYPE))
check("REGULAR → positive 而非 negative",
      d_is_positive(DCACHE_REGULAR_TYPE) and not d_is_negative(DCACHE_REGULAR_TYPE))
check("DIRECTORY ≠ AUTODIR（两个 d_is_dir 类判定必须分开）",
      d_is_directory(DCACHE_DIRECTORY_TYPE) and
      not d_is_directory(DCACHE_AUTODIR_TYPE) and
      d_is_autodir(DCACHE_AUTODIR_TYPE))
check("d_can_lookup 同时接受 DIRECTORY 与 AUTODIR",
      d_can_lookup(DCACHE_DIRECTORY_TYPE) and d_can_lookup(DCACHE_AUTODIR_TYPE))
check("d_can_lookup 拒绝 REGULAR", not d_can_lookup(DCACHE_REGULAR_TYPE))
check("d_is_file = REGULAR 或 SPECIAL",
      d_is_file(DCACHE_REGULAR_TYPE) and d_is_file(DCACHE_SPECIAL_TYPE) and
      not d_is_file(DCACHE_DIRECTORY_TYPE))
check("d_is_special 与 d_is_reg 互斥",
      d_is_special(DCACHE_SPECIAL_TYPE) and not d_is_reg(DCACHE_SPECIAL_TYPE))
check("d_is_symlink 只认 SYMLINK",
      d_is_symlink(DCACHE_SYMLINK_TYPE) and not d_is_symlink(DCACHE_REGULAR_TYPE))
check("whiteout 是独立类型，不是 negative",
      d_is_whiteout(DCACHE_WHITEOUT_TYPE) and
      not d_is_negative(DCACHE_WHITEOUT_TYPE))
check("七个类型名齐全", len(TYPE_NAME) == 7)

print("== 4. dcache 内部类型 → getdents 的 d_type ==")
check("DT_UNKNOWN == 0", DT_UNKNOWN == 0)
check("DT_FIFO/CHR/DIR/BLK = 1/2/4/6（值不连续，是 stat 模式位右移）",
      (DT_FIFO, DT_CHR, DT_DIR, DT_BLK) == (1, 2, 4, 6))
check("DT_REG/LNK/SOCK/WHT = 8/10/12/14",
      (DT_REG, DT_LNK, DT_SOCK, DT_WHT) == (8, 10, 12, 14))
check("MISS(negative) 暴露成 DT_UNKNOWN",
      DCACHE_TO_DT[DCACHE_MISS_TYPE] == DT_UNKNOWN)
check("SPECIAL 也暴露成 DT_UNKNOWN（用户态必须能处理）",
      DCACHE_TO_DT[DCACHE_SPECIAL_TYPE] == DT_UNKNOWN)
check("DIRECTORY 与 AUTODIR 都暴露成 DT_DIR",
      DCACHE_TO_DT[DCACHE_DIRECTORY_TYPE] == DT_DIR and
      DCACHE_TO_DT[DCACHE_AUTODIR_TYPE] == DT_DIR)
check("WHITEOUT → DT_WHT（overlayfs 用）",
      DCACHE_TO_DT[DCACHE_WHITEOUT_TYPE] == DT_WHT)

print("== 5. dcache：引用计数、LRU、negative ==")
c = DCache()
root = Dentry("/", DCACHE_DIRECTORY_TYPE)
a = c.insert(root, "a", Dentry("a", DCACHE_REGULAR_TYPE, inode=1))
check("d_alloc 出来的 dentry refcount 是 1（in-use）", a.refcount == 1)
check("in-use 不在 LRU 上", a not in c.lru)
c.dput(a)
check("dput 归零后才进 LRU", a.refcount == 0 and a in c.lru)
c.dget(a)
check("再 dget 又移出 LRU", a.refcount == 1 and a not in c.lru)

neg = c.negative_lookup(root, "nope")
check("查不到也建 dentry（negative）", neg is not None)
check("negative dentry 的 d_inode 是 NULL", neg.inode is None)
check("negative dentry 类型是 MISS", d_is_miss(neg.flags))
check("negative dentry 仍在哈希里（下次不用再下探）",
      c.lookup(root, "nope") is neg)

c2 = DCache()
r2 = Dentry("/", DCACHE_DIRECTORY_TYPE)
made = []
for n in ("x", "y", "z"):
    d = c2.insert(r2, n, Dentry(n, DCACHE_REGULAR_TYPE, inode=1))
    c2.dput(d)
    made.append(d)
check("dput 后三个 unused dentry 都在 LRU", len(c2.lru) == 3)
freed = c2.shrink_one()
check("收缩回收队首 x", freed == "x", str(freed))
check("回收后从哈希表摘除", c2.lru[:] == made[1:] and c2.lookup(r2, "x") is None)
check("回收后置 DCACHE_DENTRY_KILLED", made[0].flags & DCACHE_DENTRY_KILLED != 0)
check("被回收的 dentry 不在 LRU 里", made[0] not in c2.lru)

c3 = DCache()
r3 = Dentry("/", DCACHE_DIRECTORY_TYPE)
p = c3.insert(r3, "p", Dentry("p", DCACHE_REGULAR_TYPE, inode=1))
q = c3.insert(r3, "q", Dentry("q", DCACHE_REGULAR_TYPE, inode=1))
c3.dput(p)
c3.dput(q)
c3.touch_lru(q)
check("touch 置 DCACHE_REFERENCED 并移到 LRU 尾部",
      bool(q.flags & DCACHE_REFERENCED) and c3.lru[-1] is q)
check("LRU 队首是没有被 touch 的 p", c3.lru[0] is p)
freed1 = c3.shrink_one()
check("第一轮回收队首 p", freed1 == "p", str(freed1))
check("被 touch 的 q 活过这一轮", q in c3.lru and not q.killed)
check("此时它的 REFERENCED 还在（还没轮到它）",
      bool(q.flags & DCACHE_REFERENCED))
freed2 = c3.shrink_one()
check("第二轮才回收 q", freed2 == "q", str(freed2))
check("回收时先清 REFERENCED 再 kill（两轮机会）",
      not (q.flags & DCACHE_REFERENCED) and q.killed)
check("LRU 已空", c3.lru == [])

c4 = DCache()
r4 = Dentry("/", DCACHE_DIRECTORY_TYPE)
d4 = c4.insert(r4, "busy", Dentry("busy", DCACHE_REGULAR_TYPE, inode=1))
check("in-use dentry 不会被 shrink 选中", c4.shrink_one() is None)
check("它压根没进 LRU", d4 not in c4.lru)

print("== 6. struct linux_dirent64 布局 ==")
check("头部 19 字节（8+8+2+1）", DIRENT64_HEADER == 19)
check("struct 格式 '<QQHB' 正好 19 字节", struct.calcsize(DIRENT64_FMT) == 19)
rec, size = dirent64_record(ino=42, off=7, name="a", dtype=DT_REG)
check("名字 'a'：19+2=21 对齐到 24", size == 24, "%d" % size)
check("d_ino 小端在偏移 0", struct.unpack_from("<Q", rec, 0)[0] == 42)
check("d_off 在偏移 8", struct.unpack_from("<Q", rec, 8)[0] == 7)
check("d_reclen 在偏移 16 且等于总长", struct.unpack_from("<H", rec, 16)[0] == 24)
check("d_type 在偏移 18", rec[18] == DT_REG)
check("d_name 从偏移 19 起且以 NUL 结尾",
      rec[19:21] == b"a\x00")
check("dirent64_size('abc') == 24（19+4=23→24）", dirent64_size("abc") == 24)
check("dirent64_size('abcdef') == 32（19+7=26→32）",
      dirent64_size("abcdef") == 32)
check("最长名字 255 → 19+256=275→280",
      dirent64_size("n" * 255) == 280, "%d" % dirent64_size("n" * 255))
check("所有 size 都是 8 的倍数",
      all(dirent64_size("n" * i) % 8 == 0 for i in range(1, 60)))

print("== 7. getdents64 的装填与 EINVAL 时机 ==")
entries = [(1, "one", DT_REG), (2, "two", DT_DIR), (3, "three", DT_LNK)]
buf, n, rest, ret = emit_getdents(entries, 4096)
check("大缓冲一次装完 3 条", n == 3 and rest == 0)
check("返回值是写入字节数", ret == len(buf))
total = sum(dirent64_size(e[1]) for e in entries)
check("总长等于各条之和", len(buf) == total, "%d vs %d" % (len(buf), total))
recs = walk(buf)
check("walk 能解回 3 条", len(recs) == 3)
check("解出的名字正确", [r[4] for r in recs] == ["one", "two", "three"])
check("解出的 d_type 正确", [r[3] for r in recs] == [DT_REG, DT_DIR, DT_LNK])
check("解出的 d_ino 正确", [r[0] for r in recs] == [1, 2, 3])
check("最后一条的 d_off 被覆写成 ctx.pos=4", recs[-1][1] == 4,
      "实际 %d" % recs[-1][1])
check("非最后一条的 d_off 是填入时的值 1/2",
      (recs[0][1], recs[1][1]) == (1, 2))

buf, n, rest, ret = emit_getdents(entries, dirent64_size("one"))
check("缓冲只够 1 条 → 返回 1 条，不是 EINVAL", n == 1 and ret > 0)
check("剩余 2 条", rest == 2)
buf, n, rest, ret = emit_getdents(entries, dirent64_size("one") - 1)
check("缓冲连 1 条都装不下 → EINVAL", n == 0 and ret == EINVAL,
      "ret=%d" % ret)
check("EINVAL 时没有写入任何字节", buf == b"")
check("一条都装不下时剩余 3 条", rest == 3)
buf, n, rest, ret = emit_getdents(entries, 0)
check("count=0 也是 EINVAL", ret == EINVAL)

print("== 8. 大目录遍历：多次 getdents 拼接 ==")
many = [(i, "f%04d" % i, DT_REG) for i in range(1000)]
got = []
pos = 0
calls = 0
while pos < len(many):
    buf, n, rest, ret = emit_getdents(many[pos:], 512)
    if ret == EINVAL:
        break
    got.extend(r[4] for r in walk(buf))
    pos += n
    calls += 1
check("512 字节缓冲需要多次调用", calls > 1, "calls=%d" % calls)
check("拼接后顺序完整", got == ["f%04d" % i for i in range(1000)])
check("没有丢条目也没有重复", len(got) == len(set(got)) == 1000)

print()
print("TOTAL: %d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
