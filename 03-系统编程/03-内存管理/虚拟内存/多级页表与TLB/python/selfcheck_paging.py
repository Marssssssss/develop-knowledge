#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""selfcheck_paging.py —— 多级页表 / TLB / 缺页中断的断言集。

地址常量全部取自实读的 Linux 内核文档（arch/x86/x86_64/mm.rst 与 5level-paging.rst），
uffd / madvise 语义取自 man-pages。只断言确定量，不模拟真实硬件计时。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import (  # noqa: E402
    PAGE_SIZE, PAGE_BITS, ENTRIES_PER_LEVEL, LEVEL_BITS, HUGE_PAGE_SIZE,
    USER_END_4L, GUARD_HOLE_4L, USER_END_5L, DIRECT_MAP_4L, VMALLOC_4L,
    VMEMMAP_4L, KERNEL_TEXT_4L, DIRECT_MAP_5L, VMEMMAP_5L,
    VA_LIMIT_4L, PA_LIMIT_4L, VA_LIMIT_5L, PA_LIMIT_5L,
    UFFDIO_REGISTER_MODE_MISSING, UFFDIO_REGISTER_MODE_WP,
    UFFDIO_REGISTER_MODE_MINOR, UFFDIO_REGISTER_MODE_RWP,
    is_canonical, vpn_indices, page_offset, build_va,
    PageTable, tlb_reach, TLB, VMA, MINOR, MAJOR, SIGSEGV, UFFD,
)

PASS = [0]


def ok(cond, msg):
    assert cond, "FAIL: " + msg
    PASS[0] += 1
    print("  ok  %s" % msg)


def eq(a, b, msg):
    ok(a == b, "%s  (got %r, want %r)" % (msg, a, b))


# ------------------------------------------------------------ 1. 地址切分
def t_address():
    print("[1] x86-64 虚拟地址切分：4 KiB 页 + 每级 9 位")
    eq((PAGE_SIZE, ENTRIES_PER_LEVEL, LEVEL_BITS), (4096, 512, 9), "页 4 KiB / 每表 512 项 / 9 位")
    va = build_va([1, 2, 3, 4], 0x123)
    eq(vpn_indices(va), [1, 2, 3, 4], "由各级下标拼回的地址能原样切出下标")
    eq(page_offset(va), 0x123, "低 12 位是页内偏移")
    eq(vpn_indices(build_va([511, 511, 511, 511], 0xFFF)), [511] * 4, "各级下标的取值上界 511")
    eq([vpn_indices(va, 5) for _ in [0]][0][0], 0, "5 级页表在最前面多一级（PML5）")
    eq(len(vpn_indices(va, 5)), 5, "5 级切出 5 个下标")


# ------------------------------------------------------------ 2. 规范地址
def t_canonical():
    print("[2] 规范地址：高位必须是符号扩展")
    ok(is_canonical(0x00007fffffffffff), "2^47-1 是用户态规范地址")
    ok(not is_canonical(0x0000800000000000), "2^47 落入非规范空洞（内核文档里的 hole 起点）")
    ok(is_canonical(0xffff800000000000), "0xffff800000000000 是内核态规范地址")
    ok(is_canonical(0x00ffffffffffffffff, 5), "5 级下 2^56-1 仍是规范地址")
    ok(not is_canonical(0x0100000000000000, 5),
       "5 级下 0x0100000000000000 是非规范空洞（文档：+64 PB 起）")
    ok(is_canonical(USER_END_4L) and is_canonical(GUARD_HOLE_4L),
       "4 级用户空间末尾与其 guard hole 都还在规范区内")


# ------------------------------------------------------------ 3. 内核布局
def t_layout():
    print("[3] 内核虚拟内存布局（mm.rst 原文数值）")
    eq(USER_END_4L, 0x00007ffffffff000, "4 级：用户空间止于 00007ffffffff000")
    eq(GUARD_HOLE_4L - USER_END_4L + 1, PAGE_SIZE, "紧随其后是 4 kB（1 页）的 guard hole")
    eq(DIRECT_MAP_4L, 0xffff888000000000, "4 级直接映射 ffff888000000000（64 TB）")
    eq(VMALLOC_4L, 0xffffc90000000000, "vmalloc/ioremap ffffc90000000000（32 TB）")
    eq(VMEMMAP_4L, 0xffffea0000000000, "vmemmap ffffea0000000000（1 TB）")
    eq(KERNEL_TEXT_4L, 0xffffffff80000000, "内核文本 ffffffff80000000（512 MB，映射到物理 0）")
    eq(USER_END_5L, 0x00fffffffffff000, "5 级：用户空间止于 00fffffffffff000（~64 PB）")
    eq(DIRECT_MAP_5L, 0xff11000000000000, "5 级直接映射下移到 ff11000000000000（32 PB）")
    eq(VMEMMAP_5L, 0xffd4000000000000, "5 级 vmemmap ffd4000000000000（0.5 PB）")


# ------------------------------------------------------------ 4. 5 级页表
def t_five_level():
    print("[4] 5 级页表带来的容量提升（5level-paging.rst）")
    eq((VA_LIMIT_4L, PA_LIMIT_4L), (256 * (1 << 40), 64 * (1 << 40)),
       "4 级：256 TiB 虚拟 / 64 TiB 物理")
    eq((VA_LIMIT_5L, PA_LIMIT_5L), (128 * (1 << 50), 4 * (1 << 50)),
       "5 级：128 PiB 虚拟 / 4 PiB 物理")
    eq(VA_LIMIT_5L // VA_LIMIT_4L, 512, "虚拟地址空间 ×512")
    eq(PA_LIMIT_5L // PA_LIMIT_4L, 64, "物理地址空间 ×64")
    ok(USER_END_5L > USER_END_4L, "5 级的用户空间端点远高于 4 级")
    eq((USER_END_5L + 1) // (USER_END_4L + 1), 512, "用户空间从 128 TB 扩到 64 PB，正好 ×512")
    ok(0x0000800000000000 > USER_END_4L, "4 级下 47 位以上即非规范（默认不分配 47 位以上的地址）")


# ------------------------------------------------------------ 5. 遍历代价
def t_walk():
    print("[5] 页表遍历代价 = 级数 + 1（最后一次是数据访问）")
    p4, p5 = PageTable(4), PageTable(5)
    eq(p4.walk(build_va([1, 2, 3, 4]))[1], 5, "4 级：4 次查表 + 1 次数据访问")
    eq(p5.walk(build_va([1, 2, 3, 4, 5]))[1], 6, "5 级：5 次查表 + 1 次数据访问")
    eq(p5.walk(0)[1] - p4.walk(0)[1], 1, "多一级就多一次内存访问")


# ------------------------------------------------------------ 6. TLB
def t_tlb():
    print("[6] TLB 覆盖与巨页")
    eq(tlb_reach(512, PAGE_SIZE), 2 * 1024 * 1024, "512 项 × 4 KiB = 2 MiB")
    eq(tlb_reach(512, HUGE_PAGE_SIZE) // tlb_reach(512, PAGE_SIZE), 512,
       "同样项数换成 2 MiB 巨页，覆盖量 ×512")
    t = TLB(2)
    eq(t.lookup(1), False, "首次访问 miss")
    eq(t.lookup(1), True, "同一页再访问 hit")
    eq((t.hits, t.misses), (1, 1), "1 hit / 1 miss")
    t.lookup(2); t.lookup(3)
    eq(len(t.store), 2, "FIFO 替换后容量仍为 2")

    # mprotect 降权后不刷新 TLB -> 陈旧条目放行
    t2 = TLB(4)
    t2.lookup(7)
    t2.downgrade(7)                      # mprotect(PROT_READ) 只改页表
    t2.lookup(7, need_write=True)
    eq(t2.stale_allowed, 1, "未 shootdown 时写操作被陈旧 TLB 条目放行（多核下必须 IPI）")
    t2.shootdown(7)
    eq(t2.lookup(7, need_write=True), False, "shootdown 之后重新走页表，写被拦下")


# ------------------------------------------------------------ 7. 缺页
def t_faults():
    print("[7] 缺页类型：minor / SIGSEGV / COW")
    v = VMA(4)
    eq(v.access(0), MINOR, "匿名页首次访问 = minor fault（零页）")
    eq(v.access(0), None, "已 resident 且不写 -> 不缺页")
    eq(v.mincore(), [True, False, False, False], "mincore 只报告第 0 页 resident")

    v2 = VMA(2, prot="r")
    v2.resident = [True, True]
    eq(v2.access(0, write=True), SIGSEGV, "只读页上写 -> SIGSEGV（不是 minor）")
    v3 = VMA(2, prot="none")
    v3.resident = [True, True]
    eq(v3.access(0), SIGSEGV, "PROT_NONE 下连读都 -> SIGSEGV")

    # COW：fork 后双方只读，写触发 minor 并复制
    p = VMA(2); p.resident = [True, True]; p.data = [11, 22]
    c = p.fork_cow()
    eq((p.prot, c.prot), ("rw", "rw"), "fork 不改 VMA 权限，仍是 rw")
    eq((p.pte_writable, c.pte_writable), ([False, False], [False, False]),
       "fork 只清掉两边 PTE 的写位")
    eq(p.cow, [True, True], "双方页都被标记为 COW")
    eq(c.access(0, write=True), MINOR, "子进程写 -> minor fault（COW 复制，不是 SIGSEGV）")
    eq((c.cow[0], c.pte_writable[0]), (False, True), "复制后清除 COW 并恢复写位")
    eq(c.access(0, write=True), None, "再写不缺页")
    eq(p.access(0), None, "父进程读仍正常（共享期间未触发复制）")
    eq(p.access(0, write=True), MINOR, "父进程写自己的那份也触发一次 COW")


# ------------------------------------------------------------ 8. userfaultfd
def t_uffd():
    print("[8] userfaultfd 注册模式与冲突")
    v = VMA(2)
    v.register_uffd(UFFDIO_REGISTER_MODE_MISSING)
    eq(v.access(0), UFFD, "MISSING 模式：首次访问交给用户态 handler")
    v.resident[0] = True                 # handler 用 UFFDIO_COPY 填好
    eq(v.access(0), None, "填好之后不再上报")

    w = VMA(2)
    w.resident = [True, True]
    w.register_uffd(UFFDIO_REGISTER_MODE_WP)
    eq(w.access(0, write=True), UFFD, "WP 模式：写已存在的页 -> 用户态")
    eq(w.access(0), None, "读不触发 WP")

    x = VMA(2)
    try:
        x.register_uffd(UFFDIO_REGISTER_MODE_WP | UFFDIO_REGISTER_MODE_RWP)
        ok(False, "WP 与 RWP 同时注册应报 EINVAL")
    except ValueError as e:
        ok("EINVAL" in str(e), "WP 与 RWP 不能同时注册（userfaultfd(2) 原文）")
    x.register_uffd(UFFDIO_REGISTER_MODE_MISSING | UFFDIO_REGISTER_MODE_WP)
    eq(x.uffd, UFFDIO_REGISTER_MODE_MISSING | UFFDIO_REGISTER_MODE_WP,
       "MISSING | WP 是合法组合")


# ------------------------------------------------------------ 9. madvise
def t_madvise():
    print("[9] MADV_DONTNEED：匿名私有映射 -> 零页按需")
    v = VMA(2)
    v.resident = [True, True]
    v.data = [7, 8]
    eq(v.data, [7, 8], "丢弃前有数据")
    v.madvise_dontneed()
    eq(v.mincore(), [False, False], "MADV_DONTNEED 后两页都不再 resident")
    eq(v.data, [0, 0], "再次访问会得到零页（原文：zero-fill-on-demand）")
    eq(v.access(0), MINOR, "重新访问触发 minor fault（重新零填充）")


def main():
    t_address(); t_canonical(); t_layout(); t_five_level(); t_walk()
    t_tlb(); t_faults(); t_uffd(); t_madvise()
    print("\nALL PASS: %d assertions" % PASS[0])


if __name__ == "__main__":
    main()
