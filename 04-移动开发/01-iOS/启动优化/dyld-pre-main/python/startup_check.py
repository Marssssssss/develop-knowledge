#!/usr/bin/env python3
r"""dyld pre-main 启动耗时模型 —— 把四个阶段拆成可断言的量级关系。纯标准库。

权威依据(完整引用见同目录 README「参考资料」):Apple WWDC 2016 Session 406
《Optimizing App Startup Time》把 main() 之前的加载分成四个阶段:

  1. Load dylibs  —— 递归加载依赖的 dylib:读 Mach-O、验签、对每个 segment mmap()。
                     真实 iOS App 通常依赖 100~400 个库,系统库已由 dyld shared cache 预处理。
  2. Rebase/Bind  —— ASLR 让镜像落在随机基址:要修正「镜像内部指针」(Rebase,偏 IO)
                     与「指向镜像外部的符号指针」(Bind,偏 CPU)。
  3. ObjC setup   —— 注册所有 ObjC 类、把 category 方法插进方法列表、selector 去重。
  4. Initializers —— 跑每个类/分类的 +load、C/C++ 构造器函数、非平凡类型的静态全局对象。

**本文件是一个成本模型,不是测量器。** 系数是合成的,只保留各阶段的量级关系
(哪一项与什么成正比、哪一项是固定开销),用来回答「改哪个变量、能省下哪一段」。
真实机上请用 DYLD_PRINT_STATISTICS=1 打印各阶段耗时 —— 那才是唯一可信的数字来源。
"""

from __future__ import annotations

from dataclasses import dataclass

# 成本系数(合成),单位是「任意耗时单位」
C_DYLIB_FIXED = 12.0       # 每个 dylib 的**固定**开销:打开/验签/映射/登记符号
C_DYLIB_SYMBOL = 0.05      # 每个对外符号引用 —— Bind 阶段查符号表
C_REBASE_PTR = 0.02        # 每个镜像内指针 —— Rebase 阶段加 slide 偏移
C_CLASS = 0.6              # 每个 ObjC 类:类对象指针 + 类表登记
C_SELECTOR_REF = 0.004     # 每个 selector 引用:去重表插入
C_SELECTOR_UNIQUE = 0.3    # 每个唯一 selector:哈希表节点
C_CATEGORY = 0.5           # 每个 category:方法列表插入
C_LOAD_BODY = 3.0          # 每个 +load 方法体(里面做的事按 1 个单位算)
C_CTOR = 1.2               # 每个 __attribute__((constructor))
C_STATIC_GLOBAL = 1.6      # 每个非平凡类型的 C++ 静态全局对象(还要登记析构)


@dataclass
class App:
    """一个 App 在启动路径上的「形状」。"""

    name: str
    dylibs: int = 180              # 依赖的动态库数量(含嵌入的第三方动态库)
    symbols: int = 9000            # 对外符号引用总数
    global_pointers: int = 26000   # 镜像内部的全局指针(__DATA 里需要 rebase 的)
    classes: int = 1200            # ObjC 类数量
    selector_refs: int = 34000     # selector 引用总数
    categories: int = 260          # category 数量
    load_methods: int = 210        # +load 实现数量
    ctors: int = 90                # C/C++ constructor 数量
    static_globals: int = 130      # 非平凡静态全局对象数量
    launch_path_pages: int = 4200  # 启动路径上触及的内存页数(缺页成本)
    lazy_initialized: int = 0      # 首屏前才被第一次消息触发的 +initialize 数量


def default_app(name: str = "baseline") -> App:
    return App(name)


# 成本函数
def unique_selectors(app: App) -> int:
    """selector 去重后剩下的**唯一** selector 数。

    去重的意义正在于此:引用 34000 次 ≠ 34000 个不同的 selector。
    """
    return max(1, int(app.selector_refs ** 0.5 * 2 + app.classes * 0.8))


@dataclass
class Phases:
    load_dylibs: float = 0.0
    rebase_bind: float = 0.0
    objc_setup: float = 0.0
    initializers: float = 0.0

    @property
    def total(self) -> float:
        return self.load_dylibs + self.rebase_bind + self.objc_setup + self.initializers

    def as_dict(self) -> dict[str, float]:
        return {"Load dylibs": self.load_dylibs, "Rebase/Bind": self.rebase_bind,
                "ObjC setup": self.objc_setup, "Initializers": self.initializers}


def rebase_part(app: App) -> float:
    """Rebase:只与镜像内部的指针数量有关。"""
    return C_REBASE_PTR * app.global_pointers


def bind_part(app: App) -> float:
    """Bind:只与指向镜像外部的符号引用有关。"""
    return C_DYLIB_SYMBOL * app.symbols


def measure(app: App) -> Phases:
    p = Phases()
    p.load_dylibs = C_DYLIB_FIXED * app.dylibs
    p.rebase_bind = rebase_part(app) + bind_part(app)
    p.objc_setup = (C_CLASS * app.classes + C_CATEGORY * app.categories
                    + C_SELECTOR_REF * app.selector_refs
                    + C_SELECTOR_UNIQUE * unique_selectors(app))
    p.initializers = (C_LOAD_BODY * app.load_methods + C_CTOR * app.ctors
                      + C_STATIC_GLOBAL * app.static_globals)
    return p


def first_screen_cost(app: App) -> float:
    """pre-main 之后、首屏期间才发生的初始化:懒加载的 +initialize 落在这里。"""
    return C_LOAD_BODY * app.lazy_initialized


def page_fault_cost(app: App) -> float:
    """缺页成本:启动路径上每触及一个尚未驻留的页就要一次 page fault。

    二进制重排(binary reordering)减少的就是这一项 —— 它**不在**上面四段里。
    """
    return 0.05 * app.launch_path_pages


# 自检
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    print(("  [PASS] " if ok else "  [FAIL] ") + label + (f"   {detail}" if detail else ""))


def report(title: str, p: Phases) -> None:
    parts = "  ".join(f"{k}={v:.0f}" for k, v in p.as_dict().items())
    print(f"    {title:<24} {parts}   合计={p.total:.0f}")


def t01_four_phases() -> None:
    print("[1] pre-main 的四段构成")
    p = measure(default_app())
    report("baseline", p)
    check("四阶段之和 = pre-main 总耗时", abs(p.total - sum(p.as_dict().values())) < 1e-9,
          f"{p.total:.0f}")
    check("Load dylibs + Rebase/Bind 占大头 —— 两段都是「指针与库数量」的代价",
          (p.load_dylibs + p.rebase_bind) / p.total > 0.5,
          f"{(p.load_dylibs + p.rebase_bind) / p.total:.0%}")
    check("Initializers 段全是「你写的代码」—— 唯一能靠重构直接砍掉的一段",
          p.initializers / p.total < 0.3, f"{p.initializers / p.total:.0%}")


def t02_dylibs_scale() -> None:
    print("[2] dylib 数量:每个都是固定开销")
    a, b = default_app(), default_app()
    a.dylibs, b.dylibs = 100, 400
    pa, pb = measure(a), measure(b)
    report("100 个 dylib", pa)
    report("400 个 dylib", pb)
    check("400 个 dylib 的 Load dylibs 是 100 个的 4 倍(严格线性)",
          abs(pb.load_dylibs / pa.load_dylibs - 4.0) < 1e-9,
          f"{pb.load_dylibs / pa.load_dylibs:.2f}x")
    check("每个 dylib 的固定开销与它多大无关:少 60 个就线性少 60 份",
          abs((pb.load_dylibs - measure(fewer_by(b, 60)).load_dylibs)
              - C_DYLIB_FIXED * 60) < 1e-9,
          f"单个 ≈ {C_DYLIB_FIXED:.0f} 单位(合成系数)")
    check("Bind 的成本由**符号引用数**决定,与 dylib 个数无关 —— 两本账分开算",
          abs(bind_part(a) - bind_part(b)) < 1e-9,
          f"两者符号引用同为 {a.symbols},Bind 完全相同")


def fewer_by(app: App, n: int) -> App:
    """同形状、少 n 个 dylib 的副本。"""
    copy = App(**{**app.__dict__})
    copy.dylibs -= n
    return copy


def t03_objc_scale() -> None:
    print("[3] ObjC 规模:类数抬高 Rebase 与 ObjC setup")
    a, b = default_app(), default_app()
    a.classes, a.global_pointers, a.selector_refs = 1000, 22000, 28000
    b.classes, b.global_pointers, b.selector_refs = 4000, 88000, 112000
    pa, pb = measure(a), measure(b)
    report("1000 个类", pa)
    report("4000 个类", pb)
    ratio = pb.objc_setup / pa.objc_setup
    check("类数 ×4 → ObjC setup 涨 3~4 倍(类登记与 selector 去重都随规模走)",
          ratio > 3.0, f"{ratio:.2f}x")
    check("Rebase 项严格 ×4(镜像内指针数量直接乘 4)",
          abs(rebase_part(b) / rebase_part(a) - 4.0) < 1e-9,
          f"{rebase_part(b) / rebase_part(a):.2f}x")
    check("Bind 项一点没变 —— 类和外部符号无关",
          abs(bind_part(b) - bind_part(a)) < 1e-9, f"均为 {bind_part(a):.0f}")

    dup = default_app()
    dup.selector_refs = 340000                      # 引用 ×10
    r = unique_selectors(dup) / unique_selectors(a)
    check("selector 引用 ×10,唯一 selector 只涨不到 2 倍(去重把小表压瘪了)",
          r < 2.0, f"{unique_selectors(dup)} vs {unique_selectors(a)}(×{r:.2f})")
    check("所以 selector 的代价几乎全在「引用次数」上,不在「唯一个数」上",
          C_SELECTOR_REF * dup.selector_refs > C_SELECTOR_UNIQUE * unique_selectors(dup),
          f"引用项 {C_SELECTOR_REF * dup.selector_refs:.0f} vs 唯一项 {C_SELECTOR_UNIQUE * unique_selectors(dup):.0f}")


def t04_load_vs_initialize() -> None:
    print("[4] +load vs +initialize:把工作从 pre-main 挪到「真正用到时」")
    base = default_app()
    p0 = measure(base)
    lazy = default_app()
    lazy.load_methods = 0
    lazy.lazy_initialized = 30                      # 首屏前只有 30 个类被第一次发消息
    p1 = measure(lazy)
    report("210 个 +load", p0)
    report("改为懒加载 +initialize", p1)
    check("去掉 210 个 +load 后 Initializers 段直接掉一大截",
          p0.initializers - p1.initializers > C_LOAD_BODY * 200,
          f"省下 {p0.initializers - p1.initializers:.0f}")
    check("pre-main 里的初始化成本降到原来的一半以下",
          p1.initializers < p0.initializers / 2,
          f"{p1.initializers:.0f} / {p0.initializers:.0f} = {p1.initializers / p0.initializers:.0%}"
          "(剩下的 ctor 与静态全局对象没动)")
    check("代价是「推迟」而非「复制」:pre-main + 首屏的总量也更小了",
          p1.total + first_screen_cost(lazy) < p0.total + first_screen_cost(base),
          f"{p1.total + first_screen_cost(lazy):.0f} < {p0.total + first_screen_cost(base):.0f}")
    check("因为 210 个类里只有 30 个真的在首屏前被用到 —— 其余永远不必初始化",
          lazy.lazy_initialized < base.load_methods,
          f"210 → 30")


def t05_ctors_and_globals() -> None:
    print("[5] C/C++ 构造器与静态全局对象也在同一个池子里")
    a, b = default_app(), default_app()
    b.ctors, b.static_globals = 300, 400
    pa, pb = measure(a), measure(b)
    report("90 ctor / 130 全局", pa)
    report("300 ctor / 400 全局", pb)
    check("多出的成本 = 210×ctor 单价 + 270×全局对象单价(和 +load 抢同一段时间)",
          abs((pb.initializers - pa.initializers) - (C_CTOR * 210 + C_STATIC_GLOBAL * 270)) < 1e-9,
          f"多出 {pb.initializers - pa.initializers:.0f}")
    check("静态全局对象的单价比普通 constructor 更高(还要登记析构)",
          C_STATIC_GLOBAL > C_CTOR, f"{C_STATIC_GLOBAL} > {C_CTOR}")
    check("这三样( +load / ctor / 静态全局 )合起来才是 Initializers 段",
          pb.initializers == (C_LOAD_BODY * b.load_methods + C_CTOR * b.ctors
                              + C_STATIC_GLOBAL * b.static_globals),
          "砍 Initializers 要同时数这三样")


def t06_page_faults() -> None:
    print("[6] 缺页:二进制重排能省掉、但四个阶段数不到的那一项")
    a = default_app()
    before = page_fault_cost(a)
    reordered = default_app()
    reordered.launch_path_pages = int(a.launch_path_pages * 0.35)   # 重排后启动路径更紧凑
    after = page_fault_cost(reordered)
    check("重排把启动路径触及的页数压到 35%", after < before * 0.4,
          f"{before:.0f} → {after:.0f}")
    check("缺页成本不在四阶段里 —— DYLD_PRINT_STATISTICS 也看不到它",
          "page" not in str(measure(a).as_dict()).lower(), "要用 Instruments 的 Page Fault 计数看")
    check("所以「启动耗时 = pre-main 四段之和」是不完整的等式",
          measure(a).total + after > measure(a).total,
          f"还要加缺页 {after:.0f} 与 main() 之后的全部工作")


def t07_what_to_optimize() -> None:
    print("[7] 同一份预算下,动哪个变量最划算")
    base = measure(default_app())

    fewer = default_app(); fewer.dylibs -= 60                      # 60 个三方动态库并进主二进制
    p1 = measure(fewer)
    fewer_loads = default_app(); fewer_loads.load_methods = 10     # +load 几乎清空
    p2 = measure(fewer_loads)
    smaller = default_app()
    smaller.classes, smaller.global_pointers, smaller.selector_refs = 900, 20000, 28000
    p3 = measure(smaller)

    gains = {"合并 60 个 dylib": base.total - p1.total,
             "+load 降到 10 个": base.total - p2.total,
             "类数减 300": base.total - p3.total}
    for k, v in gains.items():
        print(f"    {k:<22} 省下 {v:>7.0f}")
    check("每个 dylib 是固定开销:少 60 个就线性少 60 份",
          abs(gains["合并 60 个 dylib"] - C_DYLIB_FIXED * 60) < 1e-9,
          f"{gains['合并 60 个 dylib']:.0f}")
    check("本模型的系数下「合并 60 个 dylib」收益最大 —— 单个 dylib 的固定开销最贵",
          max(gains, key=gains.get) == "合并 60 个 dylib",
          f"最大收益: {max(gains, key=gains.get)}")

    combined = default_app()
    combined.dylibs -= 60
    combined.load_methods = 10
    combined.classes, combined.global_pointers, combined.selector_refs = 900, 20000, 28000
    pc = measure(combined)
    check("三项一起改的收益 = 三项各自收益之和(成本可加,不互相打折)",
          abs((base.total - pc.total) - sum(gains.values())) < 1e-9,
          f"合计省下 {base.total - pc.total:.0f}")


if __name__ == "__main__":
    for fn in (t01_four_phases, t02_dylibs_scale, t03_objc_scale, t04_load_vs_initialize,
               t05_ctors_and_globals, t06_page_faults, t07_what_to_optimize):
        fn()
    print(f"\n断言 {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项:", FAIL)
    raise SystemExit(1 if FAIL else 0)
