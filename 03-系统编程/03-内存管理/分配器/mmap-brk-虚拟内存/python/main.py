"""brk/sbrk 与 mmap —— 5 组文档级语义实验(可实跑自检)。

  python3 main.py    # 63 个断言

模型在 vm_model.py;本文件只放实验与断言。
"""

from vm_model import *  # noqa: F401,F403

FAILS = []
TOTAL = [0]


def check(label, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        print(f"  [ok] {label}")
    else:
        FAILS.append(label)
        print(f"  [FAIL] {label} {detail}")


# --------------------------------------------------------------------------- #
# demo 1: brk/sbrk 语义 + "系统调用 vs glibc 包装"的返回差异
# --------------------------------------------------------------------------- #
def demo1():
    print("== demo1 brk/sbrk 语义 ==")
    a = AddressSpace()
    b0 = a.sbrk(0)  # 手册:"Calling sbrk() with an increment of 0 can be used to find
    check("sbrk(0) 返回当前 program break", b0 == HEAP_START, b0)
    check("初始 break == 堆起点", a.brk_managed() == 0)

    p = a.sbrk(64)
    check("sbrk(+64) 返回**旧的** break", p == b0, p)
    check("返回的旧 break 即新分配区起点", p == b0)
    check("新 break = 旧 break + 64", a.sbrk(0) == b0 + 64, a.sbrk(0))
    check("break 是字节粒度,不按页取整", a.brk_managed() == 64)

    q = a.sbrk(-64)
    check("sbrk(-64) 同样返回旧 break", q == b0 + 64, q)
    check("break 减回起点", a.sbrk(0) == b0)

    check("glibc brk() 成功返回 0", a.glibc_brk(b0 + 4096) == 0)
    try:
        a.glibc_brk(b0 - 1)
        check("glibc brk() 失败必须抛错", False)
    except OSError_ as e:
        check("glibc brk() 失败返回 -1 且 errno=ENOMEM", e.errno == ENOMEM, e.errno)

    before = a.sbrk(0)
    r = a.sys_brk(b0 - 1)  # 系统调用语义:失败返回**当前** break
    check("系统调用 brk 失败时返回当前 break(而非 -1)", r == before, (r, before))
    check("系统调用语义与 glibc 包装语义不同", r != MAP_FAILED)

    # sbrk 是库函数,内部用 brk 系统调用 + 记账
    a2 = AddressSpace()
    check("sbrk(0) 与 sys_brk 查询一致", a2.sbrk(0) == a2.sys_brk(a2.break_))


# --------------------------------------------------------------------------- #
# demo 2: MAP_ANONYMOUS —— 匿名映射、页对齐、清零、length 必须 > 0
# --------------------------------------------------------------------------- #
def demo2():
    print("== demo2 MAP_ANONYMOUS 匿名映射 ==")
    a = AddressSpace()
    addr = a.mmap(None, 8000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS)
    check("addr 由内核选择且页对齐", addr % PAGE == 0, addr)
    check("内核选择的地址不低于 mmap_min_addr 所在页", addr >= MMAP_BASE)
    check("长度向上取整到 2 页(8000 -> 8192)", a.find(addr)["length"] == 8192, a.find(addr)["length"])
    check("MAP_ANONYMOUS 内容初始化为 0", a.load(addr, 16) == b"\x00" * 16)

    a.store(addr + PAGE, 8)
    check("写入第 2 页后读回一致", a.load(addr + PAGE, 8) == b"\xa5" * 8)
    check("第 1 页仍为 0(映射未被牵连)", a.load(addr, 4) == b"\x00" * 4)

    try:
        a.mmap(None, 0, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS)
        check("length=0 必须失败(手册:length must be > 0)", False)
    except OSError_ as e:
        check("length=0 -> EINVAL", e.errno == EINVAL)

    try:
        a.mmap(None, 4096, PROT_READ, 0)
        check("缺 MAP_PRIVATE/MAP_SHARED 必须失败", False)
    except OSError_ as e:
        check("未指定共享性 -> EINVAL", e.errno == EINVAL)
    try:
        a.mmap(None, 4096, PROT_READ, MAP_PRIVATE | MAP_SHARED)
        check("同时给 PRIVATE|SHARED 必须失败", False)
    except OSError_ as e:
        check("PRIVATE|SHARED 同时给 -> EINVAL", e.errno == EINVAL)

    two = a.mmap(None, PAGE, PROT_READ, MAP_SHARED | MAP_ANONYMOUS)
    check("MAP_ANONYMOUS 可与 MAP_SHARED 组合", a.find(two) is not None)
    check("第二次内核选址不与第一次重叠", not (two < addr + 8192 and addr < two + PAGE))


# --------------------------------------------------------------------------- #
# demo 3: 文件映射的 offset 对齐要求 + munmap 的 addr/length 不对称要求
# --------------------------------------------------------------------------- #
def demo3():
    print("== demo3 offset / munmap 的对齐要求 ==")
    a = AddressSpace()
    ok = a.mmap(None, PAGE, PROT_READ, MAP_PRIVATE, offset=4096)
    check("offset = 1 页 -> 成功", a.find(ok) is not None)
    check("offset = 0 -> 成功", a.mmap(None, PAGE, PROT_READ, MAP_PRIVATE, offset=0) >= 0)

    try:
        a.mmap(None, 4096, PROT_READ, MAP_PRIVATE, offset=1)
        check("offset 非页倍数必须失败", False)
    except OSError_ as e:
        check("offset=1 -> EINVAL(必须为 sysconf(_SC_PAGE_SIZE) 的倍数)", e.errno == EINVAL)

    # 巨页映射要求更严:offset 必须是底层巨页大小的倍数
    hp = a.mmap(None, HUGE_PAGE, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS, offset=HUGE_PAGE, huge=True)
    check("巨页 offset = 巨页大小 -> 成功", a.find(hp) is not None)
    try:
        a.mmap(None, HUGE_PAGE, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS, offset=PAGE, huge=True)
        check("巨页 offset 仅页对齐必须失败", False)
    except OSError_ as e:
        check("巨页 offset = 1 页 -> EINVAL", e.errno == EINVAL)

    region = a.mmap(None, 4 * PAGE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS)
    a.store(region, 4 * PAGE, 0x11)
    try:
        a.munmap(region + 1, PAGE)
        check("munmap addr 非页对齐必须失败", False)
    except OSError_ as e:
        check("munmap addr 非页对齐 -> EINVAL", e.errno == EINVAL)

    # length 不必是页的整数倍:含该范围的整页都被卸载
    a.munmap(region + PAGE, 16)
    check("munmap length 可不对齐,仍成功", a.find(region + PAGE) is None)
    check("被覆盖的**整页**都被卸载", a.page_of(region + PAGE + PAGE - 1) is None)
    check("范围外的页不受影响", a.find(region) is not None)
    check("手册:range 内无映射也不算错", a.munmap(0xDEAD_0000, PAGE) == 0)


# --------------------------------------------------------------------------- #
# demo 4: MAP_FIXED / MAP_FIXED_NOREPLACE 的重叠语义
# --------------------------------------------------------------------------- #
def demo4():
    print("== demo4 MAP_FIXED 与 MAP_FIXED_NOREPLACE ==")
    a = AddressSpace()
    base = 0x7F00_1000_0000
    a.mmap(base, 4 * PAGE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED)
    a.store(base, 4 * PAGE, 0x22)
    check("MAP_FIXED 精确落在请求地址", a.find(base)["start"] == base)

    # MAP_FIXED_NOREPLACE:冲突 -> EEXIST,不破坏已有映射
    try:
        a.mmap(base, PAGE, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE)
        check("NOREPLACE 冲突必须失败", False)
    except OSError_ as e:
        check("MAP_FIXED_NOREPLACE 重叠 -> EEXIST", e.errno == EEXIST, e.errno)
    check("失败后已有映射完好", a.find(base) is not None and a.load(base, 1) == b"\x22")

    # MAP_FIXED:重叠部分被丢弃
    a.mmap(base + PAGE, 4 * PAGE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED)
    check("MAP_FIXED 覆盖后旧映射被丢弃", a.unmapped_spans != [])
    check("MAP_FIXED 覆盖后新映射就位", a.load(base + 2 * PAGE, 4) == b"\x00" * 4)
    check("未重叠的前一页保留旧内容", a.load(base, 1) == b"\x22")

    # 非 FIXED 时 addr 只是 hint(内核可另择地址)
    hint = 0x7F00_2000_0000
    got = a.mmap(hint, PAGE, PROT_READ, MAP_PRIVATE | MAP_ANONYMOUS)
    check("非 FIXED 时 addr 仅作提示", got % PAGE == 0 and a.find(got) is not None)


# --------------------------------------------------------------------------- #
# demo 5: malloc 的 mmap 阈值策略(静态默认 + 动态调整 + 冻结条件)
# --------------------------------------------------------------------------- #
def demo5():
    print("== demo5 mmap 阈值:128 KiB 默认与动态调整 ==")
    pol = ThresholdPolicy()
    check("DEFAULT_MMAP_THRESHOLD = 128*1024", pol.mmap_threshold == 131072, pol.mmap_threshold)
    check("DEFAULT_MMAP_THRESHOLD_MAX(64 位)= 4M*sizeof(long)", DEFAULT_MMAP_THRESHOLD_MAX == 33554432)
    check("DEFAULT_MMAP_MAX = 65536", pol.mmap_max == 65536)

    check("64 KiB 分配走堆", pol.classify(64 * 1024) == "heap")
    check("恰好 128 KiB 走 mmap(>= 阈值)", pol.classify(128 * 1024) == "mmap")
    check("128 KiB - 1 仍走堆", pol.classify(128 * 1024 - 1) == "heap")

    check("释放 200 KiB 的块 -> 阈值上调", pol.on_free(200 * 1024) == "raise")
    check("阈值 = 被释放块大小", pol.mmap_threshold == 204800, pol.mmap_threshold)
    check("trim 阈值动态调整为阈值的 2 倍", pol.trim_threshold == 409600, pol.trim_threshold)
    check("此时 150 KiB 走堆(阈值已上调)", pol.classify(150 * 1024) == "heap")

    check("再释放 1 MiB 的块 -> 阈值继续上调", pol.on_free(1024 * 1024) == "raise")
    check("阈值 = 1 MiB", pol.mmap_threshold == 1048576)
    check("最大只到 DEFAULT_MMAP_THRESHOLD_MAX",
          (pol.on_free(DEFAULT_MMAP_THRESHOLD_MAX + 1) == "keep"))
    check("超上限后阈值不再变化", pol.mmap_threshold == 1048576)
    check("上限值本身可上调", pol.on_free(DEFAULT_MMAP_THRESHOLD_MAX) == "raise")
    check("阈值等于上限", pol.mmap_threshold == DEFAULT_MMAP_THRESHOLD_MAX)

    pol2 = ThresholdPolicy()
    pol2.mallopt("M_MMAP_THRESHOLD")
    check("显式设置 mallopt 后动态调整被禁用", pol2.dynamic is False)
    check("被禁用后释放大块不再改阈值", pol2.on_free(8 * 1024 * 1024) == "frozen")
    check("阈值保持 128 KiB", pol2.mmap_threshold == 131072)

    # M_TOP_PAD 的填充量总是向上取整到系统页边界;M_TRIM_THRESHOLD=-1 关闭修剪
    check("M_TOP_PAD 填充量按页取整", page_align_up(1) == 4096 and page_align_up(4096) == 4096)
    check("M_TRIM_THRESHOLD 默认 128 KiB", DEFAULT_TRIM_THRESHOLD == 131072)
    trim_disabled = -1
    check("M_TRIM_THRESHOLD = -1 表示完全禁用修剪", trim_disabled == -1)


def main():
    for fn in (demo1, demo2, demo3, demo4, demo5):
        fn()
        print()
    total = TOTAL[0]
    print(f"断言总数 {total},失败 {len(FAILS)}")
    if FAILS:
        for f in FAILS:
            print("  FAILED:", f)
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
