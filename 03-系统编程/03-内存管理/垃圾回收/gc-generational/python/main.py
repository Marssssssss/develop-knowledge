#!/usr/bin/env python3
"""分代式 GC —— 5 组实验(可实跑自检)。

  python3 main.py

模块划分:
  gen_model.py  堆模型(eden + 两个 survivor + 老年代 + 写屏障/记忆集)
  cpy_gc.py     CPython 三代 GC 的判定逻辑模型(对应 Python/gc.c)
  harness.py    断言计数器与通用夹具
  demos.py      demo1~demo3(minor 流程 / 写屏障 / 晋升阈值)
  main.py       本文件:demo4、demo5 与入口
"""

import gc
import sys

from cpy_gc import NUM_GENERATIONS, GenState
from demos import demo1, demo2, demo3
from gen_model import YOUNG_CAPACITY, GenHeap
from harness import FAILS, TOTAL, check, force_old


def demo4():
    print("== demo4 minor vs major 的成本(实测被扫描字数) ==")
    YOUNG_CAP, OLD_CAP = 12, 2400        # 两边共用同一套堆预算
    LONG_OBJS, LONG_WORDS = 500, 4       # 长期存活集 = 2000 字
    CHURN = 4000                         # 之后产生的临时对象(1 字/个)

    def make(generational):
        return GenHeap(tenuring=2, barrier=True, generational=generational,
                       young_capacity=YOUNG_CAP, old_capacity=OLD_CAP)

    def seed(h):
        for i in range(LONG_OBJS):
            h.roots.append(h.alloc(f"L{i}", LONG_WORDS))
            h.step()

    def churn(h):
        t0 = h.traced_words
        for i in range(CHURN):
            h.alloc(f"c{i}", 1)
            h.step()
        return h.traced_words - t0

    gen, nogen = make(True), make(False)
    seed(gen)
    seed(nogen)
    check("长期存活集建好后绝大部分已晋升进老年代",
          gen.old_words() >= 1900, gen.old_words())
    check("字数守恒:极少数尾部对象还留在新生代等下一轮晋升",
          gen.old_words() + gen.young_words() == LONG_OBJS * LONG_WORDS,
          (gen.old_words(), gen.young_words()))
    check("非分代基线没有老年代,存活集就堆在堆里",
          nogen.old_words() == 0 and nogen.young_words() == LONG_OBJS * LONG_WORDS,
          (nogen.old_words(), nogen.young_words()))

    seed_gen, seed_nogen = gen.traced_words, nogen.traced_words
    minor_before = gen.minor_gcs
    gen_cost, nogen_cost = churn(gen), churn(nogen)
    churn_minors = gen.minor_gcs - minor_before

    check("两边都真的跑了收集", gen_cost > 0 and nogen_cost > 0, (gen_cost, nogen_cost))
    check("分代:churn 期间一次全量收集都没有,全是 minor",
          gen.major_gcs == 0 and churn_minors > CHURN // YOUNG_CAP - 20,
          (gen.major_gcs, churn_minors))
    check("非分代基线:只做全量收集,次数 ≥ 8",
          nogen.minor_gcs == 0 and nogen.major_gcs >= 8,
          (nogen.minor_gcs, nogen.major_gcs))
    check("基线每次全量收集都必须扫过 2000 字的存活集",
          nogen_cost > 2000 * nogen.major_gcs, (nogen_cost, nogen.major_gcs))
    check("分代每次 minor 的扫描量 ≈ 新生代容量",
          8 * churn_minors <= gen_cost <= 16 * churn_minors,
          (gen_cost, churn_minors))
    ratio = nogen_cost / gen_cost
    check("基线 / 分代 的扫描量比 > 4", ratio > 4, round(ratio, 2))
    print(f"     [info] churn 期扫描量:分代 {gen_cost} 字 vs 基线 {nogen_cost} 字"
          f"({ratio:.1f}x);预热期:分代 {seed_gen} 字 vs 基线 {seed_nogen} 字")
    check("预热期分代并不便宜(晋升要复制,这段成本被排除在对比之外)",
          seed_gen > 0, seed_gen)

    # minor 的成本只与新生代有关,**与老年代无关**
    h = make(True)
    h.roots.append(force_old(h, h.alloc("O", 8)))
    for i in range(3):
        force_old(h, h.alloc(f"P{i}", 8))
    check("老年代已有 4 个对象(32 字)", h.old_words() == 32, h.old_words())
    h.alloc("y", 1)
    t0 = h.traced_words
    h.minor()
    minor_cost = h.traced_words - t0
    t1 = h.traced_words
    h.major()
    major_cost = h.traced_words - t1
    check("minor 只扫新生代,老年代再大也不影响它", minor_cost == 1, minor_cost)
    check("major 必须扫全堆(老年代 32 字也逃不掉)", major_cost == 32, major_cost)
    check("同一堆上 major 的扫描量是 minor 的 32 倍",
          major_cost == 32 * minor_cost, (major_cost, minor_cost))


def demo5():
    print("== demo5 CPython 三代 GC 的判定逻辑对照 ==")
    s = GenState(thresholds=(5, 2, 2))
    for _ in range(5):
        s.record_alloc()
    check("count == threshold0 时还不触发(条件是严格大于)", not s.enabled(), s.counts)
    s.record_alloc()
    check("超过 threshold0 才触发", s.enabled(), s.counts)
    s.record_dealloc(3)
    check("释放会抵消分配", s.counts[0] == 3, s.counts[0])
    for _ in range(99):
        s.record_dealloc()
    check("count 不会变负(CPython: if (count > 0) count--)", s.counts[0] == 0, s.counts[0])

    for _ in range(3):
        s.record_alloc(6)
        s.collect(0)
    check("每收一次 gen0,gen1 的 count +1",
          s.counts[1] == 3, s.counts[1])
    s.record_alloc(6)
    check("gen0 被检查次数 > threshold1 后,才会收到 gen1",
          s.select_generation() == 1, (s.counts, s.select_generation()))

    # 25% long_lived 启发式:全量收集被推迟
    s2 = GenState(thresholds=(5, 2, 2))
    s2.counts = [5, 2, 3]                       # 只有最老一代超阈值,才轮得到这条启发式
    s2.long_lived_total, s2.long_lived_pending = 100, 24
    check("pending(24) < total/4(25) -> 连最老一代也跳过,本次不收集",
          s2.select_generation() == -1, s2.select_generation())
    s2.long_lived_pending = 25
    check("pending 到达 total/4 才允许收最老一代",
          s2.select_generation() == 2, s2.select_generation())
    s2.long_lived_pending = 0                   # 先把启发式挪开,再看中间代
    s2.counts[1] = 3                            # 中间代超阈值 -> 至少能退一步收 gen1
    check("中间代超阈值时不受该启发式限制,退而收 gen1",
          s2.select_generation() == 1, s2.select_generation())

    # collect() 的返回值语义 + 晋升 + garbage
    s3 = GenState()
    s3.track("dead1")
    s3.track("dead2")
    s3.track("alive", reachable=True)
    s3.track("finalized", uncollectable=True)
    rv = s3.collect(0)
    check("gc.collect() 返回值 = collected + uncollectable", rv == 2 + 0 + 1, rv)
    check("不可回收对象被移到 garbage 列表", [o.oid for o in s3.garbage] == ["finalized"])
    check("存活对象晋升到第 1 代",
          [o.oid for o in s3.objs[1]] == ["alive"], s3.objs[1])
    check("被收集的代清空", s3.objs[0] == [])
    check("stats 结构 = collections/collected/uncollectable",
          set(s3.stats[0]) == {"collections", "collected", "uncollectable"}, s3.stats[0])
    check("stats[0] 记账正确",
          s3.stats[0]["collections"] == 1 and s3.stats[0]["collected"] == 2
          and s3.stats[0]["uncollectable"] == 1, s3.stats[0])
    check("代数是三代(NUM_GENERATIONS)", NUM_GENERATIONS == 3)

    # 最老代的两种行为:留在原地 / 刚升入最老代要记 long_lived_pending
    s4 = GenState()
    o4 = s4.track("long", reachable=True)
    s4.collect(2)                      # 一次全量收集
    check("收最老代时 long_lived_total 被重置为该代大小",
          s4.long_lived_total == 1 and s4.long_lived_pending == 0,
          (s4.long_lived_total, s4.long_lived_pending))
    s4.collect(2)
    check("已是最老代的对象留在原地,不再晋升", o4.gen == 2)
    check("全量收集后 counts[2] 也被清零", s4.counts == [0, 0, 0], s4.counts)

    s5 = GenState()
    o5 = s5.track("mid", reachable=True)
    s5.collect(0)
    check("gen0 收集后存活对象晋升到 gen1", o5.gen == 1, o5.gen)
    pending_before = s5.long_lived_pending
    s5.collect(1)
    check("收 gen1 时把升入最老代的存活数计入 long_lived_pending",
          o5.gen == 2 and s5.long_lived_pending == pending_before + 1,
          (o5.gen, s5.long_lived_pending))

    # 真实解释器:实测默认阈值与源码记载的版本差异一致
    th = gc.get_threshold()
    check("gc.get_threshold() 返回三元组",
          len(th) == 3 and all(isinstance(v, int) for v in th), th)
    check("threshold1 == threshold2 == 10(3.13 前后一致)", th[1] == 10 and th[2] == 10, th)
    want = 2000 if sys.version_info >= (3, 13) else 700
    check(f"默认 threshold0 与源码一致(本解释器 "
          f"{sys.version_info.major}.{sys.version_info.minor} -> {want})", th[0] == want, th[0])
    stats = gc.get_stats()
    check("gc.get_stats() 返回 3 个字典",
          len(stats) == 3 and all(set(d) == {"collections", "collected", "uncollectable"}
                                  for d in stats), stats[0])
    old = gc.get_threshold()
    gc.set_threshold(0)
    check("threshold0 = 0 时收集被关闭", gc.get_threshold()[0] == 0)
    gc.set_threshold(*old)
    check("阈值可恢复", gc.get_threshold() == old)


def main():
    for fn in (demo1, demo2, demo3, demo4, demo5):
        fn()
        print()
    print(f"断言总数 {TOTAL[0]},失败 {len(FAILS)}")
    if FAILS:
        for f in FAILS:
            print("  FAILED:", f)
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
