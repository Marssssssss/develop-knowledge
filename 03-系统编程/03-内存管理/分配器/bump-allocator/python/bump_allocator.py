"""bump_allocator.py — 最小 bump (arena) 分配器(单块 Python 版)

工作机制与 C 版完全对应:
  - 预分配一整段 bytearray 作为底层 buffer
  - 维护 self.offset (相对 self.base 的字节数)
  - 每次分配:
      * 把当前指针向上对齐到 alignment(2 的幂)
      * 返回对齐后的内存切片(memoryview,写入后自动反映到 arena 中)
      * offset += 填充 + size
  - 释放只能整块重置(reset),不能 free 单个对象

注意:
  - alignment 必须为 2 的幂,否则 bit-AND 取整会出错
  - 返回的 memoryview 视图指向 arena buffer 内部;arena 被销毁/重置后视图失效
  - OOM 时抛 MemoryError(对外契约比 C 版更显式)
"""
import ctypes
from typing import Optional


class Arena:
    """单块 bump allocator。"""

    def __init__(self, capacity: int):
        if capacity <= 0 or (capacity & (capacity - 1)) != 0:
            # 容量也限制为 2 的幂,与 C 版做对齐约束保持一致
            raise ValueError(f"capacity must be a positive power of 2, got {capacity}")
        # bytearray 起一个连续的、可写的底层内存;与 mmap PROT_READ|PROT_WRITE 对应
        self._buf      = bytearray(capacity)
        self._base     = 0                # bytearray 已经从 0 开始,这里仅表示偏移基准
        self.capacity  = capacity
        self.offset    = 0

    def reset(self) -> None:
        """整块重置:offset 归零;老的 memoryview 在逻辑上视为失效。"""
        self.offset = 0

    def _align_up(self, x: int, alignment: int) -> int:
        """把 x 向上取整到 alignment 的倍数(alignment 必为 2 的幂)。"""
        return (x + alignment - 1) & ~(alignment - 1)

    def alloc(self, size: int, alignment: int = 8) -> memoryview:
        """分配 size 字节,按 alignment 向上对齐。

        返回的 memoryview 指向 arena 内部 buffer;写入会立即可见。
        arena.reset() 之后该 memoryview 仍然指向同样地址,但语义上属于复用。
        """
        if alignment <= 0 or (alignment & (alignment - 1)) != 0:
            raise ValueError(f"alignment must be a positive power of 2, got {alignment}")

        # 1) 当前 offset 对应的绝对地址 → 向上对齐
        current = self._base + self.offset
        aligned = self._align_up(current, alignment)
        pad     = aligned - current

        # 2) 检查剩余容量
        if self.offset + pad + size > self.capacity:
            raise MemoryError(
                f"arena OOM: need {pad + size} bytes, "
                f"only {self.capacity - self.offset} bytes free"
            )

        # 3) 推进 offset 并返回视图
        self.offset += pad + size
        # memoryview(self._buf[aligned:aligned+size]) 是只读别名;
        # 用 bytearray 切片得到可写视图,直接写入会改 self._buf
        return memoryview(self._buf)[aligned: aligned + size]


# ----------------------- demo -----------------------

def demo_basic(a: Arena) -> None:
    print(f"[1] basic allocations (default alignment = {ctypes.sizeof(ctypes.c_void_p)} bytes)")

    view_i = a.alloc(4, alignment=4)            # 模拟 int
    view_i[0:4] = b"\x2a\x00\x00\x00"           # 小端 int 42
    print(f"    int*  -> 4-byte aligned, value={int.from_bytes(view_i, 'little')}")

    view_d = a.alloc(8, alignment=8)            # double 用 8 字节对齐
    view_d[0:8] = b"\x1f\x85\xeb\x51\xb8\x1e\x09\x40"  # 3.14 IEEE 754 LE
    print(f"    double* -> value={struct_unpack_double(view_d):.2f}")

    # struct Data 12 bytes (const char* 8B + unsigned 4B);只需 8 字节对齐
    view_s = a.alloc(16, alignment=8)
    print(f"    struct Data -> 16B allocated at offset-related slice")
    print(f"    arena offset after 3 allocs = {a.offset} bytes")


def struct_unpack_double(view: memoryview) -> float:
    import struct
    return struct.unpack('<d', view)[0]


def demo_alignment(a: Arena) -> None:
    print("\n[2] explicit alignment (32-byte)")

    # 先 alloc 1 字节 char,把 offset 推到非对齐位置
    a.alloc(1, alignment=1)
    before = a.offset

    # 用 32 字节对齐查看 padding
    view = a.alloc(4, alignment=32)
    after = a.offset

    print(f"    char* offset before = {before - 1}")
    print(f"    int*  32-byte aligned; padding = {after - before - 4} bytes "
          f"(offset {before} -> {after})")
    view[0:4] = b"\x07\x00\x00\x00"
    print(f"    int value = {int.from_bytes(view, 'little')}")


def demo_reset_reuse(a: Arena) -> None:
    print("\n[3] reset & reuse — bump allocator has no per-object free")

    before = a.offset
    a.reset()
    print(f"    reset(): offset {before} -> 0")

    p1 = a.alloc(4, alignment=4); p1[0:4] = b"\x64\x00\x00\x00"  # 100
    p2 = a.alloc(4, alignment=4); p2[0:4] = b"\xc8\x00\x00\x00"  # 200
    print(f"    reuse: p1={int.from_bytes(p1, 'little')}, "
          f"p2={int.from_bytes(p2, 'little')}  (offset={a.offset})")
    print(f"    note: previously-written int=42, double=3.14 etc. are logically freed")


def demo_oob_check(a: Arena) -> None:
    print("\n[4] OOM check — request too large for remaining capacity")
    remaining = a.capacity - a.offset
    huge      = a.capacity  # 故意请求与整段 capacity 等大,超出 remaining
    print(f"    remaining = {remaining} bytes, capacity = {a.capacity} bytes")
    try:
        a.alloc(huge, alignment=1)
        print("    unexpected: alloc succeeded??")
    except MemoryError as e:
        print(f"    arena.alloc({huge}) raised MemoryError as expected ✓ ({e})")


def main() -> None:
    print("=== bump (arena) allocator demo (Python) ===")
    CAP = 64 * 1024  # 64 KiB
    print(f"capacity = {CAP // 1024} KiB")
    a = Arena(CAP)

    demo_basic(a)
    demo_alignment(a)
    demo_reset_reuse(a)
    demo_oob_check(a)

    print("\n[ok] arena discarded (GC will free bytearray).")


if __name__ == "__main__":
    main()
