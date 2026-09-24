"""演示:在一个 16 字节类的块上走一遍分配/释放,并把结果地址拆开看。

运行: python main.py
"""

from nanov2_const import (nano_common_good_size, size_class_from_size,
                          size_from_size_class, slots_by_size_class,
                          SLOT_BUMP, SLOT_FULL, SLOT_CAN_MADVISE)
from nanov2_model import Layout, Block, Arena, allocation_block_index

COOKIE = 0x2A


def main():
    print("== 尺寸类 ==")
    for size in (1, 16, 17, 100, 200, 256, 257):
        sc = size_class_from_size(size)
        good = nano_common_good_size(size)
        print("  malloc(%4d) -> good_size=%3d class=%2d(%3d 字节) %s"
              % (size, good, sc, size_from_size_class(sc),
                 "nanov2" if good <= 256 else "交给 helper zone"))

    print("\n== 各尺寸类的块内槽位与浪费 ==")
    for sc in range(16):
        n = slots_by_size_class(sc)
        waste = 16384 - n * size_from_size_class(sc)
        print("  class %2d: %3d 字节 x %4d 槽, 浪费 %3d 字节" %
              (sc, size_from_size_class(sc), n, waste))

    print("\n== 一个 16 字节类的块 ==")
    blk = Block(0)
    ptrs = []
    for _ in range(5):
        ptrs.append(blk.allocate())
    print("  连分 5 次 -> 槽位", ptrs, "next_slot=SLOT_BUMP free_count=%d"
          % blk.free_count)
    blk.free(ptrs[1])
    print("  释放槽 %d   -> next_slot=%d(1-based) free_count=%d"
          % (ptrs[1], blk.next_slot, blk.free_count))
    again = blk.allocate()
    print("  再分配     -> 槽位 %d(LIFO,复用刚释放的槽)" % again)

    for _ in range(1024 - 5):
        blk.allocate()
    print("  填满后     -> next_slot=SLOT_FULL(%d) free_count=%d(回绕)"
          % (SLOT_FULL, blk.free_count))
    print("  再分配     ->", blk.allocate())

    print("\n== 地址拆解(iOS 变体) ==")
    lay = Layout(ios=True, signature=0x6)
    arena = Arena(aslr_cookie=COOKIE)
    block = arena.first_block_for_size_class(0)
    slot = 3
    addr = lay.encode(offset=slot * 16, block=block, arena=1)
    d = lay.decode(addr)
    print("  class 0 首块 = %d(逻辑 %d 与 cookie 0x%x 异或后)"
          % (block, block ^ COOKIE, COOKIE))
    print("  槽 %d 的地址 = 0x%x -> offset=0x%x block=%d arena=%d"
          % (slot, addr, d["offset"], d["block"], d["arena"]))
    print("  由块号反查尺寸类 =", arena.size_class_for_block(block))

    print("\n== CPU -> 当前块下标 ==")
    print("  ", [allocation_block_index(c) for c in (0, 1, 63, 64, 65)])

    print("\n== 放空一个已停用的块 ==")
    b = Block(7)
    b.allocate()
    b.in_use = False
    print("  free ->", b.free(0), "next_slot=SLOT_CAN_MADVISE(%d)" % SLOT_CAN_MADVISE)


if __name__ == "__main__":
    main()
