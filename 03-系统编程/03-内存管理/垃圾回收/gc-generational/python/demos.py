#!/usr/bin/env python3
"""分代式 GC 的 demo1~demo3:minor GC 流程 / 写屏障 / 晋升阈值。"""

from gen_model import TENURING_THRESHOLD, GenHeap
from harness import check, force_old


def demo1():
    print("== demo1 一次 minor GC 的完整流程 ==")
    h = GenHeap(tenuring=2)
    a = h.alloc("A", 2)
    b = h.alloc("B", 2)
    h.roots.append(a)
    h.store(a, "toB", b)                 # young -> young 不需要写屏障
    temps = [h.alloc(f"t{i}", 1) for i in range(4)]
    check("分配都落在 eden", len(h.eden) == 6, len(h.eden))
    check("写屏障只记 老->新,故此时没有脏卡", len(h.cards) == 0, h.cards)

    h.minor()
    check("minor GC 会计次", h.minor_gcs == 1)
    check("eden 被整块清空", len(h.eden) == 0, h.eden)
    check("from-survivor 也清空", h.survivors[1 - h.cur] == [])
    check("存活者 A/B 被复制进 survivor", [o.oid for o in h.survivors[h.cur]] == ["A", "B"])
    check("复制后年龄 +1", (a.age, b.age) == (1, 1), (a.age, b.age))
    check("未达晋升阈值,仍属新生代", a.gen == 0 and b.gen == 0)
    check("垃圾 t0..t3 被回收(不在任何空间里)",
          all(id(t) not in h.resident() for t in temps))
    check("成本代理量 = 被扫描的新生代字数(2+2+4)",
          h.traced_words == 8, h.traced_words)

    h.minor()
    check("第二次 minor 后年龄到 2,达到 MaxTenuringThreshold",
          (a.age, b.age) == (2, 2), (a.age, b.age))
    check("达到阈值 -> 晋升到老年代", [o.oid for o in h.old] == ["A", "B"])
    check("晋升后 gen 置为 1", a.gen == 1 and b.gen == 1)
    check("survivor 重新变空(对象都走了)", h.survivors[h.cur] == [])
    check("晋升计数 = 2", h.promoted == 2, h.promoted)
    check("survivor 空间在两个半区之间轮换", h.cur == 0, h.cur)

    # 第三次:老年代对象再指向新生代 -> 这一次必须靠写屏障
    c = h.alloc("C", 1)
    h.store(a, "toC", c)
    check("老->新 引用被写屏障记进脏卡", (a.oid, "toC") in h.cards, h.cards)
    check("记忆集里出现该槽", (a, "toC") in h.remembered)
    h.minor()
    check("被记忆集保护的 C 存活", c.age == 1 and id(c) in h.resident())
    check("本轮没有悬空引用", h.dangling() == [], h.dangling())


def demo2():
    print("== demo2 写屏障/记忆集:分代 GC 的正确性前提 ==")
    out = {}
    for name, barrier in (("with", True), ("without", False)):
        h = GenHeap(tenuring=2, barrier=barrier)
        owner = force_old(h, h.alloc("O", 1))     # O 已在老年代
        h.roots.append(owner)
        child = h.alloc("C", 1)
        if barrier:
            h.store(owner, "child", child)
        else:
            h.raw_store(owner, "child", child)    # 无屏障写
        h.minor()
        out[name] = (h, owner, child)

    hw, ow, cw = out["with"]
    hn, on, cn = out["without"]
    check("有屏障:老->新 引用进脏卡/记忆集", len(hw.cards) == 1 and len(hw.remembered) == 1)
    check("无屏障:脏卡与记忆集都是空的", len(hn.cards) == 0 and len(hn.remembered) == 0)
    check("有屏障:C 作为额外根被复制,存活", id(cw) in hw.resident() and cw.age == 1)
    check("无屏障:C 不是根,被当成垃圾回收掉", id(cn) not in hn.resident())
    check("无屏障时老年代字段仍指向它 -> 悬空引用",
          len(hn.dangling()) == 1, hn.dangling())
    check("悬空三元组 = (owner=O, field=child, target=C)",
          hn.dangling() == [("O", "child", "C")], hn.dangling())
    check("有屏障时悬空集合为空(这正是写屏障存在的理由)", hw.dangling() == [])

    # 对照:全堆收集**不需要**记忆集
    h3 = GenHeap(tenuring=2, barrier=False)
    o3 = force_old(h3, h3.alloc("O", 1))
    h3.roots.append(o3)                            # 老年代对象本身是根
    c3 = h3.raw_store(o3, "child", h3.alloc("C", 1)).fields["child"]
    h3.major()
    check("major 不需要记忆集(major_gcs 计次)", h3.major_gcs == 1 and len(h3.remembered) == 0)
    check("major 扫全堆,故 C 无需脏卡也能存活", id(c3) in h3.resident())
    check("major 后无悬空", h3.dangling() == [], h3.dangling())


def demo3():
    print("== demo3 晋升阈值(MaxTenuringThreshold)的取舍 ==")
    copies, old_words = {}, {}
    for th in (1, 2, 3):
        h = GenHeap(tenuring=th)
        l = h.alloc("L", 3)
        h.roots.append(l)
        n = 0
        while l.gen == 0 and n < 5:
            h.minor()
            n += 1
        copies[th], old_words[th] = n, h.old_words()
    check("阈值=1:第一次 minor 直接晋升", copies[1] == 1, copies)
    check("阈值=2:第二次 minor 才晋升", copies[2] == 2, copies)
    check("阈值=3:第三次 minor 才晋升", copies[3] == 3, copies)
    check("晋升前的复制次数 == 阈值(阈值越高,新生代内搬运越多)",
          copies[1] < copies[2] < copies[3])
    check("阈值越低,老年代增长越快", old_words[1] >= old_words[3], old_words)
    check("本模型里三个阈值最终都把它送进老年代", all(w >= 3 for w in old_words.values()))

    # 阈值的另一面:survivor 峰值占用
    peak = {}
    for th in (2, 3):
        h = GenHeap(tenuring=th)
        l = h.alloc("L", 2)
        h.roots.append(l)
        p = 0
        while l.gen == 0:
            h.minor()
            p = max(p, sum(o.words for o in h.survivors[h.cur]))
        peak[th] = p
    check("阈值越高,survivor 峰值占用越大(存活者被扣留得更久)",
          peak[2] <= peak[3], peak)
    check("模型的默认晋升阈值 == TENURING_THRESHOLD",
          GenHeap().tenuring == TENURING_THRESHOLD == 2, GenHeap().tenuring)
