#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pprof 过滤/裁剪语义的自检（模型在 pprof_filter.py，全部断言对照 google/pprof 源码）

运行：python selfcheck_filter.py
"""
from pprof_filter import (Line, Loc, Sample, Profile, filter_samples_by_name,
                          show_from, filter_tags_by_name, filter_samples_by_tag,
                          flat_cum, discard_low_frequency_nodes,
                          trim_low_frequency_edges, build_edges, flame_children)

PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1


def L(*funcs):
    return Loc([Line(f, f + ".go") for f in funcs])


def build():
    """三条栈（叶在前）：main→work→alloc 占 60；main→work 占 30；main→gc 占 10"""
    a, w, m, g = L("alloc"), L("work"), L("main"), L("gc")
    p = Profile([Sample([a, w, m], 60), Sample([w, m], 30), Sample([g, m], 10)])
    return p, a, w, m, g


def check_proto_order():
    p, a, w, m, g = build()
    ok(p.samples[0].locs[0] is a, "profile.proto：叶必须位于 location_id[0]")
    ok(p.samples[0].locs[-1] is m, "根帧必须位于栈列表末位")
    ok(p.total == 100, "样本总量 = 60+30+10 = 100")


def check_focus():
    p, a, w, m, g = build()
    fm, im, _, _ = filter_samples_by_name(p, focus="work")
    ok(fm and not im, "-focus 命中了样本")
    ok([s.value for s in p.samples] == [60, 30], "-focus=work 只保留经过 work 的两条栈")
    ok(p.total == 90, "-focus=work 后总量 100 → 90")

    p, *_ = build()
    fm, im, _, _ = filter_samples_by_name(p, focus="nonexistent")
    ok(not fm and not im, "focus 无匹配 ⇒ fm=False（pprof 会打印 matched no samples）")
    ok(p.samples == [], "focus 无匹配 ⇒ 一条样本都不剩")


def check_ignore_beats_focus():
    """ignore 优先：同时命中 focus 与 ignore 的样本直接丢弃"""
    p, a, w, m, g = build()
    filter_samples_by_name(p, focus="work", ignore="alloc")
    ok([s.value for s in p.samples] == [30],
       "focus=work + ignore=alloc：含 alloc 的 60 那条被丢弃，只剩 30")

    p, *_ = build()
    filter_samples_by_name(p, ignore="alloc")
    ok([s.value for s in p.samples] == [30, 10], "-ignore=alloc 丢弃含 alloc 的样本")


def check_hide_show_line_level():
    """hide/show 是 **行级** 裁剪：命中的内联行被摘掉，摘空了整个 Location 才消失"""
    p = Profile([Sample([L("leaf", "helper"), L("mid"), L("root")], 10)])
    filter_samples_by_name(p, hide="helper")
    names = [ln.func for loc in p.samples[0].locs for ln in loc.lines]
    ok(names == ["leaf", "mid", "root"], "hide 只摘命中行，leaf 仍在")

    p = Profile([Sample([L("only"), L("root")], 10)])
    filter_samples_by_name(p, hide="only")
    names = [ln.func for loc in p.samples[0].locs for ln in loc.lines]
    ok(names == ["root"], "hide 把整个 Location 摘空 ⇒ 该帧从栈上消失")

    p = Profile([Sample([L("only")], 10)])
    filter_samples_by_name(p, hide="only")
    ok(p.samples == [], "栈上所有帧都被摘空 ⇒ 整条样本丢弃")

    p = Profile([Sample([L("leaf", "helper"), L("mid"), L("root")], 10)])
    filter_samples_by_name(p, show="helper")
    names = [ln.func for loc in p.samples[0].locs for ln in loc.lines]
    ok(names == ["helper"], "show 只留命中行：只剩 helper")


def check_show_from():
    """filter.go 注释里的例子：栈 [A, B, C, B]（A 为根，展示顺序）"""
    def mk():
        return Profile([Sample([L("B"), L("C"), L("B"), L("A")], 10)])

    def top_down(s):
        names = [ln.func for loc in s.locs for ln in loc.lines]
        return list(reversed(names))

    p = mk(); ok(show_from(p, "B") and top_down(p.samples[0]) == ["B", "C", "B"],
                 "ShowFrom(B) ⇒ [B, C, B]（丢掉更浅的 A）")
    p = mk(); ok(show_from(p, "C") and top_down(p.samples[0]) == ["C", "B"],
                 "ShowFrom(C) ⇒ [C, B]")
    p = mk(); ok(show_from(p, "A") and top_down(p.samples[0]) == ["A", "B", "C", "B"],
                 "ShowFrom(A) ⇒ 整条保留")
    p = mk(); ok(show_from(p, "D") is False and p.samples == [],
                 "ShowFrom(D) 无匹配 ⇒ 样本被丢弃")
    ok(show_from(mk(), None) is False, "show_from=nil 返回 False 且不改动 profile")


def check_cum_cutoff():
    """graph.go：abs(cum) < cutoff 才丢弃，等于临界值的节点 **保留**"""
    p, a, w, m, g = build()
    flat, cum = flat_cum(p)
    ok(flat == {"alloc": 60, "work": 30, "gc": 10}, "flat：只有叶帧记自用量")
    ok(cum == {"alloc": 60, "work": 90, "main": 100, "gc": 10}, "cum：栈上出现即计入")
    kept = discard_low_frequency_nodes(cum, 100, 0.005)      # cutoff = 0.5
    ok(kept == {"alloc", "work", "main", "gc"}, "nodefraction=0.005 ⇒ 全部保留")
    kept = discard_low_frequency_nodes(cum, 100, 0.05)       # cutoff = 5.0
    ok(kept == {"alloc", "work", "main", "gc"}, "cutoff=5，cum=10 的 gc 仍保留")
    kept = discard_low_frequency_nodes(cum, 100, 0.10)       # cutoff = 10.0
    ok(kept == {"alloc", "work", "main", "gc"}, "cum 恰等于 cutoff(10) 的 gc **保留**")
    kept = discard_low_frequency_nodes(cum, 100, 0.101)      # cutoff = 10.1
    ok(kept == {"alloc", "work", "main"}, "cutoff=10.1 ⇒ gc 被裁掉")


def check_edges_and_flame():
    p, a, w, m, g = build()
    edges = build_edges(p)
    ok(edges == {("work", "alloc"): 60, ("main", "work"): 90, ("main", "gc"): 10},
       "边权重 = 相邻帧共同出现的样本值之和")
    kept, dropped = trim_low_frequency_edges(edges, 100, 0.5)   # cutoff = 50
    ok(dropped == 1, "edgefraction=0.5 ⇒ 只有权重 10 的 main→gc 被裁掉")
    ok(sorted(kept.values()) == [60, 90], "保留下 60 与 90 两条边")
    kept, dropped = trim_low_frequency_edges(edges, 100, 0.95)  # cutoff = 95
    ok(kept == {} and dropped == 3, "cutoff=95 ⇒ 三条边全裁，图上全是虚线边")

    cum, kids = flame_children(p)
    ok(cum["main"] == 100, "火焰图根框宽度 = 总量")
    ok([c for c, _ in kids["main"]] == ["work", "gc"],
       "孩子按宽度降序：work(90) 在 gc(10) 之前")
    ok(kids["work"] == [("alloc", 60)], "work 的孩子只有 alloc")


def check_tags():
    # proto 里 Sample.label 是 map<string, []string>，值本身就是列表
    p = Profile([Sample([L("a")], 10, labels={"route": ["/login"]}),
                 Sample([L("b")], 20, labels={"route": ["/health"]})])
    filter_samples_by_tag(p, tagfocus="/login")
    ok([s.value for s in p.samples] == [10], "-tagfocus 只留命中的样本")

    p = Profile([Sample([L("a")], 10, labels={"route": ["/login"], "env": ["prod"]})])
    filter_samples_by_tag(p, tagfocus="/login", tagignore="prod")
    ok(p.samples == [], "tagfocus 与 tagignore 同时命中 ⇒ 丢弃（README 明示）")

    p = Profile([Sample([L("a")], 10, labels={"route": ["/x"], "debug": ["1"]})])
    sm, hm = filter_tags_by_name(p, taghide="debug")
    ok(hm and p.samples[0].labels == {"route": ["/x"]}, "-taghide 摘掉命中的标签")

    p = Profile([Sample([L("a")], 10, labels={"route": ["/x"]})])
    fm, im = filter_samples_by_tag(p, tagfocus="/nope")
    ok(not fm and p.samples == [], "tagfocus 无匹配 ⇒ fm=False 且样本清空")


def check_order():
    """applyFocus 的顺序：focus/ignore/hide/show → show_from → tag"""
    p = Profile([Sample([L("leaf"), L("skipme"), L("root")], 10)])
    filter_samples_by_name(p, hide="skipme")
    ok([ln.func for loc in p.samples[0].locs for ln in loc.lines] == ["leaf", "root"],
       "hide 摘帧后父子关系被压缩 ⇒ 调用图上出现虚线边")
    ok(show_from(p, "root") and len(p.samples[0].locs) == 2, "show_from 在 hide 之后执行")


if __name__ == "__main__":
    for fn in (check_proto_order, check_focus, check_ignore_beats_focus,
               check_hide_show_line_level, check_show_from, check_cum_cutoff,
               check_edges_and_flame, check_tags, check_order):
        fn()
    print("PASS %d assertions" % PASS)
