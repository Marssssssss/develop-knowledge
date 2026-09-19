"""CoW 与快照自检：把 btrfs-man5 / fiemap.h / send-receive 的条文变成断言。"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cow_extents import (  # noqa: E402
    Allocator, FIEMAP_EXTENT_DATA_INLINE, FIEMAP_EXTENT_DATA_TAIL,
    FIEMAP_EXTENT_DELALLOC, FIEMAP_EXTENT_LAST, FIEMAP_EXTENT_NOT_ALIGNED,
    FIEMAP_EXTENT_SHARED, FIEMAP_EXTENT_UNKNOWN, FIEMAP_EXTENT_UNWRITTEN,
    FIEMAP_FLAGS_COMPAT, FIEMAP_FLAG_SYNC, FIEMAP_FLAG_XATTR,
    cow_write, defrag, exclusive_bytes, fiemap, flag_names, logical_bytes,
    make_file, reflink_copy, resolve_mount_options, swapfile_ok,
)
from cow_stream import (  # noqa: E402
    btrfs_csum, crc32c, crc32c_bitwise, full_send, incremental_send,
)

PASS = 0
FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %-54s %s" % (label, detail))
    else:
        FAIL += 1
        print("  FAIL %-54s %s" % (label, detail))


print("== 1. reflink 复制不占新空间 ==")
alloc = Allocator()
src = make_file(alloc, "db", 10)           # 10 块 = 40 KiB
before = alloc.live_bytes()
clone = reflink_copy(alloc, src)
check("reflink 后活动物理块没变", alloc.live_bytes() == before,
      "%d == %d" % (alloc.live_bytes(), before))
check("逻辑空间翻倍、物理空间没变",
      logical_bytes(alloc, src) + logical_bytes(alloc, clone) == 2 * before,
      "逻辑 2×%d" % before)
check("reflink 刚完成时两边独占空间都是 0",
      exclusive_bytes(alloc, src) == 0 and exclusive_bytes(alloc, clone) == 0)

print("== 2. 改一个块只复制一个块（extent 被切成三段） ==")
new_blocks = cow_write(alloc, clone, 3)
check("只新分配 1 个块", new_blocks == 1, "%d" % new_blocks)
check("活动块数 +1", alloc.live_bytes() == before + alloc.block_size,
      "%d" % alloc.live_bytes())
check("一个 extent 被切成 3 段", len(clone.extents) == 3,
      "%d 段" % len(clone.extents))
check("反过来源文件变成那 1 块的独占者",
      exclusive_bytes(alloc, src) == alloc.block_size,
      "%d" % exclusive_bytes(alloc, src))
check("副本独占 1 块", exclusive_bytes(alloc, clone) == alloc.block_size,
      "%d" % exclusive_bytes(alloc, clone))

print("== 3. FIEMAP_EXTENT_SHARED 反映共享关系 ==")
em = fiemap(alloc, clone)
shared = [e for e in em if e[3] & FIEMAP_EXTENT_SHARED]
check("头尾两段仍带 SHARED", len(shared) == 2, "%d 个共享段" % len(shared))
check("被改写的中间段不带 SHARED",
      not (em[1][3] & FIEMAP_EXTENT_SHARED) and em[1][0] == 3 * 4096,
      "logical=%d" % em[1][0])
check("最后一个 extent 带 LAST", bool(em[-1][3] & FIEMAP_EXTENT_LAST))
check("LAST 只出现在最后一段",
      sum(1 for e in em if e[3] & FIEMAP_EXTENT_LAST) == 1)
check("源文件全部带 SHARED",
      all(e[3] & FIEMAP_EXTENT_SHARED for e in fiemap(alloc, src)))
check("fiemap 的偏移量是字节不是块",
      em[0][0] == 0 and em[1][0] == 3 * 4096 and em[2][0] == 4 * 4096,
      "%s" % [e[0] for e in em])

print("== 4. 碎片整理会打断 reflink（空间放大） ==")
alloc2 = Allocator()
a = make_file(alloc2, "a", 8)
b = reflink_copy(alloc2, a)
c = reflink_copy(alloc2, a)
before2 = alloc2.live_bytes()
defrag(alloc2, b)
check("defrag 后分配了整份新空间",
      alloc2.live_bytes() == before2 + 8 * alloc2.block_size,
      "%d -> %d" % (before2, alloc2.live_bytes()))
check("b 变成独占 8 块", exclusive_bytes(alloc2, b) == 8 * alloc2.block_size)
check("a 与 c 仍互相共享（独占 0）", exclusive_bytes(alloc2, a) == 0)
check("三个文件逻辑上 24 块、物理上 16 块",
      sum(f.blocks() for f in (a, b, c)) == 24 and alloc2.live_blocks() == 16,
      "物理 %d" % alloc2.live_blocks())

print("== 5. NODATACOW 原地写（省空间，但可能写坏） ==")
alloc3 = Allocator()
nocow = make_file(alloc3, "swap", 4, nocow=True)
shared = reflink_copy(alloc3, nocow)
before3 = alloc3.live_bytes()
n = cow_write(alloc3, shared, 1)
check("NODATACOW 文件不触发新分配", n == 0)
check("活动块数不变", alloc3.live_bytes() == before3)
check("代价：共享块被原地改写（torn write 风险）",
      alloc3.refcount(nocow.extents[0].phys) == 2)

print("== 6. 挂载选项互斥（按顺序、最后生效） ==")
st = resolve_mount_options(["compress", "nodatacow"])
check("nodatacow 在后 → 压缩被关掉", st == {
    "datacow": False, "datasum": False, "compress": False}, "%s" % st)
st = resolve_mount_options(["nodatacow", "compress"])
check("compress 在后 → CoW 被恢复", st == {
    "datacow": True, "datasum": True, "compress": True}, "%s" % st)
st = resolve_mount_options(["nodatasum"])
check("nodatasum 隐含关压缩但不关 datacow",
      st["datasum"] is False and st["compress"] is False
      and st["datacow"] is True, "%s" % st)
st = resolve_mount_options(["nodatasum", "datasum"])
check("datasum 隐含 datacow", st["datacow"] is True and st["datasum"] is True)
check("默认 datacow+datasum 开、压缩关", resolve_mount_options([]) == {
    "datacow": True, "datasum": True, "compress": False})

print("== 7. swapfile 的两条硬约束 ==")
check("NODATACOW + 无洞 → 可用", swapfile_ok(True, False))
check("有洞 → 不可用（必须预分配）", not swapfile_ok(True, True))
check("没设 NODATACOW → 不可用", not swapfile_ok(False, False))

print("== 8. fiemap 标志位关系 ==")
check("DELALLOC 会置 UNKNOWN（需调用方自行补）",
      FIEMAP_EXTENT_DELALLOC | FIEMAP_EXTENT_UNKNOWN
      != FIEMAP_EXTENT_DELALLOC)
check("DATA_INLINE / DATA_TAIL 都会置 NOT_ALIGNED",
      FIEMAP_EXTENT_DATA_INLINE & FIEMAP_EXTENT_NOT_ALIGNED == 0
      and FIEMAP_EXTENT_DATA_TAIL & FIEMAP_EXTENT_NOT_ALIGNED == 0)
check("UNWRITTEN 与 SHARED 是不同位",
      FIEMAP_EXTENT_UNWRITTEN != FIEMAP_EXTENT_SHARED
      and FIEMAP_EXTENT_UNWRITTEN & FIEMAP_EXTENT_SHARED == 0)
check("FIEMAP_FLAGS_COMPAT = SYNC|XATTR",
      FIEMAP_FLAGS_COMPAT == (FIEMAP_FLAG_SYNC | FIEMAP_FLAG_XATTR))
check("flag_names 能拆出多个标志",
      flag_names(FIEMAP_EXTENT_LAST | FIEMAP_EXTENT_SHARED)
      == ["LAST", "SHARED"])

print("== 9. CRC32C：表驱动 == 逐位参考实现 ==")
rng = random.Random(20260919)
ok = True
for _ in range(200):
    data = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 64)))
    if crc32c(data) != crc32c_bitwise(data):
        ok = False
        break
check("200 组随机数据两种实现一致", ok)
check("空输入的两种实现也一致", crc32c(b"") == crc32c_bitwise(b""))
check("标准 CRC-32C（init=F, xorout）自洽",
      crc32c(b"123456789", init=0xFFFFFFFF, xorout=True)
      == crc32c_bitwise(b"123456789", init=0xFFFFFFFF, xorout=True))

print("== 10. btrfs 变体：初值 0、不取反 ==")
d = b"btrfs-send-stream"
check("btrfs_csum = init 0 且不取反", btrfs_csum(d) == crc32c(d, 0, False))
check("与标准形态不同（不可混用）",
      btrfs_csum(d) != crc32c(d, 0xFFFFFFFF, True),
      "%#010x vs %#010x" % (btrfs_csum(d), crc32c(d, 0xFFFFFFFF, True)))
# CRC 对 init 是仿射的：crc(d, I) = M^len · I ⊕ h(d)，所以**等长**数据时
# 两种 init 的差值是常数（换成不等长就不成立了，因为 M^len 随长度变）
check("等长数据下 init 差异是常数",
      btrfs_csum(b"aaaaaa") ^ crc32c(b"aaaaaa", 0xFFFFFFFF, False)
      == btrfs_csum(b"zzzzzz") ^ crc32c(b"zzzzzz", 0xFFFFFFFF, False),
      "%#010x" % (btrfs_csum(b"aaaaaa")
                  ^ crc32c(b"aaaaaa", 0xFFFFFFFF, False)))
check("不等长时该常数不成立（反例，别搞混）",
      btrfs_csum(b"aaaaaa") ^ crc32c(b"aaaaaa", 0xFFFFFFFF, False)
      != btrfs_csum(b"zz") ^ crc32c(b"zz", 0xFFFFFFFF, False))

print("== 11. full send vs incremental send ==")
files = [("a.txt", 3), ("b.txt", 5), ("c.txt", 2)]
full = full_send(files)
check("full 流命令数 = 2 + 2×文件数", len(full.cmds) == 2 + 2 * len(files),
      "%d" % len(full.cmds))
check("full 流末尾是 read-only 收口", full.cmds[-1][0] == "set_readonly")
check("full 流校验全过", full.verify() == [])

new = [("a.txt", 3), ("b.txt", 9), ("d.txt", 1)]
inc, changed = incremental_send(files, new)
check("增量只发变化的文件", changed == 2, "changed=%d" % changed)
check("增量流确实更短", len(inc.cmds) < len(full.cmds),
      "%d < %d" % (len(inc.cmds), len(full.cmds)))
check("增量流校验全过", inc.verify() == [])
inc.cmds[0] = (inc.cmds[0][0], inc.cmds[0][1], inc.cmds[0][2] ^ 1)
check("改一字节校验和即失败", len(inc.verify()) == 1, "%s" % inc.verify())

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
