"""稀疏文件自检：把 lseek(2) / fallocate(2) / fiemap.h 的条文变成断言。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sparse_file import (  # noqa: E402
    BLOCK, FALLOC_FL_COLLAPSE_RANGE, FALLOC_FL_INSERT_RANGE,
    FALLOC_FL_KEEP_SIZE, FALLOC_FL_PUNCH_HOLE, FALLOC_FL_ZERO_RANGE,
    FIEMAP_EXTENT_LAST, FIEMAP_EXTENT_UNWRITTEN, SEEK_DATA, SEEK_HOLE,
    SparseFile,
)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %-52s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-52s %s" % (label, detail))


def err_of(fn):
    try:
        fn()
    except OSError as e:
        return str(e).split(":")[0]
    return None


def build():
    """0 号块有数据、1 号块是洞、2 号块有数据，size=12288。"""
    f = SparseFile()
    f.write(0, BLOCK)
    f.write(2 * BLOCK, BLOCK)
    return f


print("== 1. SEEK_DATA / SEEK_HOLE 的基本走向 ==")
f = build()
check("文件大小 = 3 个块", f.size == 3 * BLOCK, "%d" % f.size)
check("逻辑 12288 字节、实际只占 8192", f.apparent_bytes() == 12288
      and f.allocated_bytes() == 8192, "%d" % f.allocated_bytes())
check("SEEK_DATA 从 0 起 → 0", f.lseek(0, SEEK_DATA) == 0)
check("SEEK_HOLE 从 0 起 → 4096", f.lseek(0, SEEK_HOLE) == BLOCK)
check("SEEK_DATA 从洞里起 → 8192", f.lseek(BLOCK, SEEK_DATA) == 2 * BLOCK)
check("已经在洞里还问洞 → 原样返回 offset", f.lseek(BLOCK, SEEK_HOLE) == BLOCK)
check("洞中间（4500）也原样返回", f.lseek(4500, SEEK_HOLE) == 4500)

print("== 2. 文件末尾的隐式洞 ==")
check("最后一个洞的 SEEK_HOLE 是 EOF",
      f.lseek(2 * BLOCK, SEEK_HOLE) == f.size)
check("EOF 处问 SEEK_DATA → ENXIO",
      err_of(lambda: f.lseek(f.size, SEEK_DATA)) == "ENXIO")
check("越过 EOF 问 SEEK_DATA → ENXIO",
      err_of(lambda: f.lseek(f.size + 1, SEEK_DATA)) == "ENXIO")
check("越过 EOF 问 SEEK_HOLE → ENXIO",
      err_of(lambda: f.lseek(f.size + 1, SEEK_HOLE)) == "ENXIO")

print("== 3. fiemap 与 UNWRITTEN ==")
em = f.fiemap()
check("两个 extent", len(em) == 2, "%s" % (em,))
check("只有最后一个 extent 带 LAST",
      [e[2] & FIEMAP_EXTENT_LAST != 0 for e in em] == [False, True])
check("真实数据不带 UNWRITTEN",
      all(not (e[2] & FIEMAP_EXTENT_UNWRITTEN) for e in em))

print("== 4. 预分配：KEEP_SIZE 与向上取整 ==")
g = SparseFile()
n = g.fallocate(0, 0, 100)            # mode=0, offset=0, len=100（同一块内）
check("跨不跨块都至少分配 1 块", n == 1 and g.allocated_blocks() == 1)
check("不带 KEEP_SIZE 时 size 变大", g.size == 100, "%d" % g.size)
h = SparseFile()
n = h.fallocate(FALLOC_FL_KEEP_SIZE, 0, 10 * BLOCK)
check("带了 KEEP_SIZE size 不变", h.size == 0, "%d" % h.size)
check("但空间真的分了 10 块", h.allocated_blocks() == 10)
n = h.fallocate(0, 4090, 100)         # 跨块：4090..4190 落在两块里
check("请求 100 字节却按块占了 2 块", n == 2, "%d" % n)
check("预分配的块带 UNWRITTEN",
      all(e[2] & FIEMAP_EXTENT_UNWRITTEN for e in h.fiemap()))

print("== 5. 打洞：PUNCH_HOLE 必须 OR KEEP_SIZE ==")
p = build()
freed = p.fallocate(FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, 0, BLOCK)
check("打掉 1 块", freed == 1, "%d" % freed)
check("文件大小不变（洞也是文件的一部分）", p.size == 3 * BLOCK)
check("实际占用降到 1 块", p.allocated_blocks() == 1)
check("打洞区间读出来是 0", p.read(0) == 0 and p.read(100) == 0)
check("单独给 PUNCH_HOLE → EINVAL",
      err_of(lambda: build().fallocate(FALLOC_FL_PUNCH_HOLE, 0, BLOCK))
      == "EINVAL")

print("== 6. ZERO_RANGE 转成 unwritten ==")
z = build()
z.fallocate(FALLOC_FL_ZERO_RANGE, 0, BLOCK)
check("原数据块变成 unwritten（只有元数据 IO）",
      z.fiemap()[0][2] & FIEMAP_EXTENT_UNWRITTEN != 0)
check("但空间仍然占着", z.allocated_blocks() == 2,
      "%d" % z.allocated_blocks())

print("== 7. COLLAPSE_RANGE 的三条 EINVAL ==")
c = SparseFile(size=5 * BLOCK)
for b in range(5):
    c.data.add(b)
check("粒度不整 → EINVAL",
      err_of(lambda: c.fallocate(FALLOC_FL_COLLAPSE_RANGE, BLOCK, 100))
      == "EINVAL")
check("触及 EOF → EINVAL（该用 ftruncate）",
      err_of(lambda: c.fallocate(FALLOC_FL_COLLAPSE_RANGE, 4 * BLOCK, BLOCK))
      == "EINVAL")
check("与其他标志并用 → EINVAL",
      err_of(lambda: c.fallocate(
          FALLOC_FL_COLLAPSE_RANGE | FALLOC_FL_KEEP_SIZE, BLOCK, BLOCK))
      == "EINVAL")
c.fallocate(FALLOC_FL_COLLAPSE_RANGE, BLOCK, BLOCK)
check("成功：size 减一块", c.size == 4 * BLOCK, "%d" % c.size)
check("成功：不留洞，后面的块整体前移",
      sorted(c.data) == [0, 1, 2, 3], "%s" % sorted(c.data))

print("== 8. INSERT_RANGE 是 COLLAPSE 的反操作 ==")
i = SparseFile(size=4 * BLOCK)
for b in range(4):
    i.data.add(b)
check("offset 达到 EOF → EINVAL",
      err_of(lambda: i.fallocate(FALLOC_FL_INSERT_RANGE, 4 * BLOCK, BLOCK))
      == "EINVAL")
check("粒度不整 → EINVAL",
      err_of(lambda: i.fallocate(FALLOC_FL_INSERT_RANGE, BLOCK, 100))
      == "EINVAL")
i.fallocate(FALLOC_FL_INSERT_RANGE, BLOCK, BLOCK)
check("成功：size 增一块", i.size == 5 * BLOCK, "%d" % i.size)
check("成功：插入处变成洞，后面的块后移",
      sorted(i.data) == [0, 2, 3, 4], "%s" % sorted(i.data))
check("插入的洞读出来是 0 且不占空间",
      i.read(BLOCK) == 0 and i.allocated_blocks() == 4)

print("== 9. 最简实现的退化（手册原文允许的写法） ==")


def degenerate_lseek(f, offset, whence):
    """手册：最简实现可以让 SEEK_HOLE 恒返回 EOF、SEEK_DATA 恒返回 offset。"""
    if whence == SEEK_HOLE:
        return f.size
    return offset


def copy_ranges(f, lseek_fn):
    """备份工具：沿 data/hole 交替只拷有数据的段。返回要拷的字节数。

    踩到文件末尾的洞时 SEEK_DATA 会报 ENXIO —— 这**不是**错误，而是"扫完了"的信号，
    必须靠它收尾（这正是很多实现的 bug 来源）。
    """
    off, copied = 0, 0
    while off < f.size:
        try:
            d = lseek_fn(f, off, SEEK_DATA)
        except OSError as e:
            if "ENXIO" in str(e):
                break
            raise
        if d >= f.size:
            break
        h = lseek_fn(f, d, SEEK_HOLE)
        copied += h - d
        off = h
    return copied


big = SparseFile(size=16 * BLOCK)
big.data.add(0)
real = copy_ranges(big, SparseFile.lseek)
dumb = copy_ranges(big, degenerate_lseek)
check("真实语义只拷 1 块", real == BLOCK, "%d" % real)
check("退化实现把整个文件当数据", dumb == big.size, "%d" % dumb)
check("退化实现不省空间（但结果仍正确）", dumb > real,
      "%d > %d" % (dumb, real))

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
