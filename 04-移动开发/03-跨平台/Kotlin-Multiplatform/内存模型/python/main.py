"""Kotlin/Native 内新存模型的自检(纯标准库,直接 python3 main.py 运行)。

三个实验,每个都对应权威文档里写过的一条因果链:
  1. stable refs —— 长循环里跨 interop 边界的临时对象,不包 autoreleasepool 会一直堆;
  2. 全局属性初始化时机 —— legacy 在程序启动时初始化,新模型改成"首次访问所属文件时";
  3. AtomicReference 引用环 —— 引用计数永远降不到 0,追踪式 GC 才能整体回收。
"""

import sys

from mm_model import (GlobalInit, build_two_cycle, loop_with_temporaries,
                      reclaim_by_refcount, reclaim_by_tracing)

PASS = 0
FAIL = 0
FAILED = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s | %s" % (label, detail))


# ===========================================================================
# 实验一:stable refs 与 autoreleasepool
# ===========================================================================
N = 1000

peak_bad, drains_bad, held_bad = loop_with_temporaries(N, use_autoreleasepool=False)
peak_good, drains_good, held_good = loop_with_temporaries(N, use_autoreleasepool=True)

check("不包 autoreleasepool 时根集合峰值 = 循环次数", peak_bad == N, str(peak_bad))
check("不包 autoreleasepool 时一次都没排空", drains_bad == 0, str(drains_bad))
check("不包 autoreleasepool 时循环结束仍扣着 N 个临时对象", held_bad == N, str(held_bad))
check("包 autoreleasepool 后峰值降到 1", peak_good == 1, str(peak_good))
check("包 autoreleasepool 后排空次数 = 循环次数", drains_good == N, str(drains_good))
check("包 autoreleasepool 后循环结束占用归零", held_good == 0, str(held_good))
check("峰值相差 N 倍(即文档所说「这个数一直涨」的量化形态)",
      peak_bad == peak_good * N, "%d vs %d" % (peak_bad, peak_good))

# 根集合单调增长(文档描述的"持续增长"现象)
roots = []
peak_series = []
for i in range(1, 6):
    peak, _, held = loop_with_temporaries(i * 100, use_autoreleasepool=False)
    peak_series.append(peak)
    roots.append(held)
check("根集合随循环长度线性增长", peak_series == [100, 200, 300, 400, 500],
      str(peak_series))
check("增长序列严格单调递增",
      all(b > a for a, b in zip(roots, roots[1:])), str(roots))

# GC 真正跑起来时才会回收;这解释"为什么包了 pool 才有界"
from mm_model import InteropRootSet
rs = InteropRootSet()
for _ in range(50):
    rs.create_temporary()
check("GC 未运行时引用数不下降", rs.stable_refs == 50, str(rs.stable_refs))
freed = rs.gc_collect(reachable=["kept"])
check("GC 跑起来后跨边界临时对象被清掉 49 个", freed == 49 and rs.stable_refs == 1,
      "%d / %d" % (freed, rs.stable_refs))

# ===========================================================================
# 实验二:全局属性的初始化时机
# ===========================================================================
FILES = {"config.kt": ["DEFAULT_TIMEOUT", "RETRY_LIMIT"],
         "net.kt": ["BASE_URL"]}
ALL = [g for names in FILES.values() for g in names]

legacy = GlobalInit(eager=True)
legacy.startup(ALL)
check("legacy:启动时就把全部 3 个全局初始化完", legacy.initialized == set(ALL),
      str(sorted(legacy.initialized)))
check("legacy:启动阶段就有 3 条 init 日志",
      len([e for e in legacy.log if e[0] == "init"]) == 3, str(legacy.log))
legacy.access_file("config.kt", FILES["config.kt"])
check("legacy:之后再访问文件不会新增 init",
      len([e for e in legacy.log if e[0] == "init"]) == 3, str(len(legacy.log)))

modern = GlobalInit(eager=False)
modern.startup(ALL)
check("新模型:启动时一个全局都不初始化", modern.initialized == set(), str(modern.initialized))
check("新模型:启动日志只有一条 lazy 说明",
      len(modern.log) == 1 and modern.log[0][1].startswith("lazy"), str(modern.log))

modern.access_file("config.kt", FILES["config.kt"])
check("访问 config.kt 只初始化它自己的两个全局",
      modern.initialized == {"DEFAULT_TIMEOUT", "RETRY_LIMIT"},
      str(sorted(modern.initialized)))
check("net.kt 的全局仍然未初始化", "BASE_URL" not in modern.initialized)
modern.access_file("config.kt", FILES["config.kt"])
check("同一文件重复访问不会重复初始化",
      len([e for e in modern.log if e[0] == "init"]) == 2, str(modern.log))
modern.access_file("net.kt", FILES["net.kt"])
check("访问 net.kt 后 3 个全局才全部就绪",
      modern.initialized == set(ALL), str(sorted(modern.initialized)))

# @EagerInitialization 是文档给出的、在新模型下"要求启动时初始化"的办法
pinned = GlobalInit(eager=False, eager_marked=["BOOT_TIMESTAMP"])
pinned.startup(["BOOT_TIMESTAMP", "LAZY_ONE"])
check("@EagerInitialization 标注的全局在启动时初始化",
      pinned.initialized == {"BOOT_TIMESTAMP"}, str(sorted(pinned.initialized)))
check("未标注的仍保持惰性", "LAZY_ONE" not in pinned.initialized)

# ===========================================================================
# 实验三:引用环与回收
# ===========================================================================
cycle = build_two_cycle()
check("环内互相引用故计数都是 1",
      [n.refcount for n in cycle] == [1, 1], str([n.refcount for n in cycle]))
check("引用计数把 AtomicReference 环判为不可回收(泄漏)",
      reclaim_by_refcount(build_two_cycle()) == [], str(reclaim_by_refcount(build_two_cycle())))
check("追踪式 GC 把整个环一起回收",
      reclaim_by_tracing(build_two_cycle()) == ["A", "B"],
      str(reclaim_by_tracing(build_two_cycle())))
check("有外部引用时追踪式 GC 不回收",
      reclaim_by_tracing(build_two_cycle(), external_refs=1) == [],
      str(reclaim_by_tracing(build_two_cycle(), external_refs=1)))
check("有外部引用时两种算法结论一致",
      reclaim_by_refcount(build_two_cycle(), external_refs=1) == []
      and reclaim_by_tracing(build_two_cycle(), external_refs=1) == [])

# 无环图上引用计数是对的 —— 差异只出在环上
from mm_model import Node


def build_chain():
    a, b = Node("A"), Node("B")
    a.edges.append(b)
    a.refcount, b.refcount = 0, 1     # 只有 B 有入边
    return [a, b]


check("无环链上引用计数能正确释放两个节点",
      reclaim_by_refcount(build_chain()) == ["A", "B"], str(reclaim_by_refcount(build_chain())))
check("无环链上追踪式 GC 结论相同",
      reclaim_by_tracing(build_chain()) == ["A", "B"], str(reclaim_by_tracing(build_chain())))
check("差异只来自环:有环时两算法结论不同",
      reclaim_by_refcount(build_two_cycle()) != reclaim_by_tracing(build_two_cycle()))


def build_three_cycle():
    a, b, c = Node("A"), Node("B"), Node("C")
    a.edges.append(b)
    b.edges.append(c)
    c.edges.append(a)
    for node in (a, b, c):
        node.refcount = 1
    return [a, b, c]


check("三元环同样被引用计数漏掉", reclaim_by_refcount(build_three_cycle()) == [])
check("三元环被追踪式 GC 整体回收",
      reclaim_by_tracing(build_three_cycle()) == ["A", "B", "C"],
      str(reclaim_by_tracing(build_three_cycle())))
check("三元环里只要有一个外部引用就整体存活",
      reclaim_by_tracing(build_three_cycle(), external_refs=1) == [])

# ===========================================================================
# 交叉核对:模型与文档结论一致性的元断言
# ===========================================================================
check("环泄漏的比例是 100%(2 环与 3 环都是 0/(环内节点数))",
      len(reclaim_by_refcount(build_two_cycle())) == 0
      and len(reclaim_by_refcount(build_three_cycle())) == 0)

print("=" * 62)
print("Kotlin/Native 内存模型自检:通过 %d 项,失败 %d 项" % (PASS, FAIL))
if FAILED:
    for line in FAILED:
        print("  [FAIL] " + line)
    sys.exit(1)
print("全部通过")
