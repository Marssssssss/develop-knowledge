#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pprof 调用图 / 火焰图视图的过滤与裁剪语义（对照 google/pprof 源码的忠实移植）

被对照的官方实现（本轮实际读过）：
  * profile/profile.proto      —— "The leaf is at location_id[0]"，即栈是 **叶在前**
  * profile/filter.go          —— FilterSamplesByName / ShowFrom / FilterTagsByName
  * internal/driver/driver_focus.go —— applyFocus 的执行顺序与 tag 过滤
  * internal/driver/commands.go     —— nodecount/nodefraction/edgefraction 帮助文本
  * internal/graph/graph.go    —— getNodesAboveCumCutoff（cum 口径，严格小于才丢弃）
  * doc/README.md              —— 调用图配色/边宽语义、火焰图宽度语义

运行：python pprof_graph_filter.py   （全部断言通过则打印 PASS 计数）
"""
import re
from collections import defaultdict

# ---------------------------------------------------------------- 模型

class Line:
    """对应 profile.Line：一行 = 一个（可能被内联的）函数 + 文件名"""
    def __init__(self, func, filename):
        self.func, self.filename = func, filename


class Loc:
    """对应 profile.Location：一个地址，可含多行（内联帧）"""
    _next_id = 1

    def __init__(self, lines, obj=None):
        self.id = Loc._next_id
        Loc._next_id += 1
        self.lines = list(lines)          # 顺序 = 调用顺序（内联展开）
        self.obj = obj                    # 对应 Mapping.File（目标文件名）

    @property
    def names(self):
        return [ln.func for ln in self.lines]

    def matches_name(self, rx):
        for ln in self.lines:
            if rx.search(ln.func) or rx.search(ln.filename):
                return True
        return bool(self.obj and rx.search(self.obj))

    def unmatched_lines(self, rx):
        if self.obj and rx.search(self.obj):
            return []                      # 命中 Mapping 时整行组丢弃
        return [ln for ln in self.lines
                if not (rx.search(ln.func) or rx.search(ln.filename))]

    def matched_lines(self, rx):
        if self.obj and rx.search(self.obj):
            return list(self.lines)
        return [ln for ln in self.lines
                if rx.search(ln.func) or rx.search(ln.filename)]


class Sample:
    def __init__(self, locs, value, labels=None, num_labels=None):
        self.locs = list(locs)             # 叶在前
        self.value = value
        self.labels = dict(labels or {})
        self.num_labels = dict(num_labels or {})


class Profile:
    def __init__(self, samples):
        self.samples = list(samples)

    @property
    def locations(self):
        seen, out = set(), []
        for s in self.samples:
            for l in s.locs:
                if l.id not in seen:
                    seen.add(l.id)
                    out.append(l)
        return out

    @property
    def total(self):
        return sum(s.value for s in self.samples)


def compile_opt(v):
    return re.compile(v) if v else None


# ------------------------------------------------- profile/filter.go 移植

def focused_and_not_ignored(locs, m):
    """filter.go: 命中 ignore 立即返回 False；否则要求至少一个 focus 命中"""
    f = False
    for loc in locs:
        if loc.id in m:
            if m[loc.id]:
                f = True                   # 继续找，可能后面还有 ignored 的
            else:
                return False               # ignore 优先
    return f


def filter_samples_by_name(prof, focus=None, ignore=None, hide=None, show=None):
    """FilterSamplesByName 的忠实移植，返回 (fm, im, hm, hnm)"""
    focus, ignore, hide, show = map(compile_opt, (focus, ignore, hide, show))
    if focus is None and ignore is None and hide is None and show is None:
        return True, False, False, False   # 缺 focus 视作命中
    fm = im = hm = hnm = False
    focus_or_ignore, hidden = {}, {}
    for loc in prof.locations:
        if ignore is not None and loc.matches_name(ignore):
            im = True
            focus_or_ignore[loc.id] = False
        elif focus is None or loc.matches_name(focus):
            fm = True
            focus_or_ignore[loc.id] = True
        # 注意顺序：focus/ignore 判定先于 hide/show 的行裁剪
        if hide is not None:
            loc.lines = loc.unmatched_lines(hide)
            if loc.lines:
                hm = True
            else:
                hidden[loc.id] = True
        if show is not None:
            loc.lines = loc.matched_lines(show)
            if loc.lines:
                hnm = True
            else:
                hidden[loc.id] = True

    kept = []
    for s in prof.samples:
        if not focused_and_not_ignored(s.locs, focus_or_ignore):
            continue
        if hidden:
            locs = [l for l in s.locs if l.id not in hidden]
            if not locs:
                continue                   # 全被隐藏 ⇒ 整条样本丢弃
            s.locs = locs
        kept.append(s)
    prof.samples = kept
    return fm, im, hm, hnm


def last_matched_line_index(loc, rx):
    for i in range(len(loc.lines) - 1, -1, -1):
        ln = loc.lines[i]
        if rx.search(ln.func) or rx.search(ln.filename):
            return i
    return -1


def show_from(prof, showfrom):
    """ShowFrom：丢弃比「最靠根的那个匹配帧」更浅的帧；无匹配则整条样本丢弃"""
    rx = compile_opt(showfrom)
    if rx is None:
        return False
    matched_locs, matched = set(), False
    for loc in prof.locations:
        if loc.obj and rx.search(loc.obj):
            matched_locs.add(loc.id)
            matched = True
            continue
        i = last_matched_line_index(loc, rx)
        if i >= 0:
            loc.lines = loc.lines[:i + 1]
            matched_locs.add(loc.id)
            matched = True

    kept = []
    for s in prof.samples:
        # 叶在前 ⇒ 从下标 len-1（根侧）往叶侧扫，命中的第一个就是「最浅的匹配」
        for i in range(len(s.locs) - 1, -1, -1):
            if s.locs[i].id in matched_locs:
                s.locs = s.locs[:i + 1]
                kept.append(s)
                break
    prof.samples = kept
    return matched


def filter_tags_by_name(prof, tagshow=None, taghide=None):
    """FilterTagsByName：保留命中 show 且不命中 hide 的标签"""
    show, hide = compile_opt(tagshow), compile_opt(taghide)
    sm = hm = False
    for s in prof.samples:
        for key in list(s.labels):
            ms, mh = (show is None or show.search(key)), (hide is not None and hide.search(key))
            if ms:
                sm = True
            if mh:
                hm = True
            if not ms or mh:
                del s.labels[key]
    return sm, hm


def filter_samples_by_tag(prof, tagfocus=None, tagignore=None):
    """driver_focus.go 注释：tagfocus 与 tagignore 同时命中 ⇒ 丢弃"""
    f, ig = compile_opt(tagfocus), compile_opt(tagignore)
    if f is None and ig is None:
        return True, False
    kept, fm, im = [], False, False
    for s in prof.samples:
        hit_f = f is not None and any(f.search(k) or any(f.search(v) for v in vs)
                                      for k, vs in s.labels.items())
        hit_i = ig is not None and any(ig.search(k) or any(ig.search(v) for v in vs)
                                       for k, vs in s.labels.items())
        if hit_f:
            fm = True
        if hit_i:
            im = True
        if hit_f and not hit_i:
            kept.append(s)
    prof.samples = kept
    return fm, im


# --------------------------------------------- internal/graph 的裁剪口径

def flat_cum(prof):
    """flat = 该帧作为叶的样本值之和；cum = 栈上出现过的样本值之和"""
    flat, cum = defaultdict(int), defaultdict(int)
    for s in prof.samples:
        names = []
        for loc in s.locs:
            names += [ln.func for ln in loc.lines]
        if not names:
            continue
        flat[names[0]] += s.value
        for n in set(names):
            cum[n] += s.value
    return flat, cum


def discard_low_frequency_nodes(cum, total, nodefraction):
    """graph.go：getNodesAboveCumCutoff —— abs(cum) < cutoff 才丢弃（临界值保留）"""
    cutoff = total * nodefraction
    return {n for n, v in cum.items() if abs(v) >= cutoff - 1e-9}


def trim_low_frequency_edges(edges, total, edgefraction):
    cutoff = total * edgefraction
    kept, dropped = {}, 0
    for (a, b), w in edges.items():
        if abs(w) >= cutoff - 1e-9:
            kept[(a, b)] = w
        else:
            dropped += 1
    return kept, dropped


def build_edges(prof):
    """边 (父, 子) 权重 = 相邻两帧同时出现的样本值之和"""
    edges = defaultdict(int)
    for s in prof.samples:
        names = []
        for loc in s.locs:
            names += [ln.func for ln in loc.lines]
        for i in range(len(names) - 1):
            edges[(names[i + 1], names[i])] += s.value   # 叶在前 ⇒ i+1 是父
    return edges


def flame_children(prof):
    """火焰图：每个框的宽度 = 该帧出现过的样本值之和（cum）；孩子按宽度降序"""
    flat, cum = flat_cum(prof)
    kids = defaultdict(list)
    for s in prof.samples:
        names = []
        for loc in s.locs:
            names += [ln.func for ln in loc.lines]
        top_down = list(reversed(names))          # 展示用：根在前
        for i in range(len(top_down) - 1):
            kids[top_down[i]].append(top_down[i + 1])
    out = {}
    for parent, cs in kids.items():
        uniq = sorted(set(cs), key=lambda c: (-cum[c], c))
        out[parent] = [(c, cum[c]) for c in uniq]
    return cum, out


# ---------------------------------------------------------------- 自检
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
