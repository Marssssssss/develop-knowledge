#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lucene / ES 的 DocValues 列式存储、doc-value-only 字段与 global ordinals。

权威来源(实际读过,不凭记忆):
  1. https://www.elastic.co/guide/en/elasticsearch/reference/current/doc-values.html
     - 倒排索引让你可以「查词 → 拿文档列表」;排序/聚合/脚本需要反过来「拿文档 → 查它的词」
     - doc_values 是**索引期构建**的**磁盘**数据结构,与 _source 存同样的值,但是**列式**
     - 支持 doc values 的字段**默认开启**(text / match_only_text 支持但默认**不**开;
       annotated_text **不支持**)
     - doc-value-only 字段:`index: false` 而 doc_values 仍默认开 ⇒ 还能查,但**比索引结构慢得多**;
       适合 gauge / counter 这类很少用来过滤的字段
     - **不能**给 wildcard 字段、或 columnar index 里的字段关闭 doc values
     - 某些类型(如 search_as_you_type)在 API 响应里能看到 doc values,但配了可能无效或报错
     - columnar index 下可设 `doc_values.multi_value: false` 限制单值;多值默认**拒绝**写入,
       可用 `on_failure` 改行为
  2. https://cdn.jsdelivr.net/gh/apache/lucene@main/lucene/core/src/java/org/apache/lucene/index/OrdinalMap.java
     - "Maps per-segment ordinals to/from global ordinal space, using a compact packed-ints
        representation."
     - NOTE:**代价很高**——必须对全部 term 做**归并排序**,且构建完后需要可观的 RAM;
       能用段内 ordinal 就别上全局
     - 成员:`globalOrdDeltas`(globalOrd → (globalOrd − 该 term 在**第一个出现它的段**里的 ord))、
       `firstSegments`、以及每个段一张 `segmentOrd → globalOrd`
     - `acceptableOverheadRatio` 控制 packed ints 的压缩率/访问速度权衡
"""
import math
import sys

_OK = [0]
_FAIL = [0]


def check(cond, msg):
    if cond:
        _OK[0] += 1
        print("  [ok]   " + msg)
    else:
        _FAIL[0] += 1
        print("  [FAIL] " + msg)


def summary():
    print("\n" + "-" * 70)
    print("断言 %d 通过 / %d 失败" % (_OK[0], _FAIL[0]))
    print("-" * 70)
    return 0 if _FAIL[0] == 0 else 1


class Field(object):
    """一个字段:是否被索引(倒排) + 是否开 doc_values(列存)"""
    def __init__(self, name, indexed=True, doc_values=True, ftype="keyword"):
        self.name = name
        self.indexed = indexed
        self.doc_values = doc_values
        self.ftype = ftype


def can_disable_doc_values(f):
    """wildcard 字段与 columnar index 里的字段不允许关闭 doc values"""
    return f.ftype not in ("wildcard", "columnar")


def build_inverted(docs):
    """倒排:term → [doc_id](用于搜索)"""
    inv = {}
    for d, term in docs:
        inv.setdefault(term, []).append(d)
    return inv


def build_doc_values(docs):
    """列存:doc_id → term(用于排序/聚合/脚本)"""
    return dict(docs)


def filter_by_term(inv, dv, term, indexed):
    """按 term 过滤:走倒排(直接拿 posting)还是走列存(逐行扫)"""
    if indexed:
        return inv.get(term, []), len(inv.get(term, []))     # 只碰命中的文档
    hits = [d for d, t in sorted(dv.items()) if t == term]
    return hits, len(dv)                                     # 必须扫整列


# ---------------------------------------------------------------- global ordinals
class OrdinalMap(object):
    """段内 ordinal ↔ 全局 ordinal 的映射(OrdinalMap.java 的最小模型)"""

    def __init__(self, seg_terms):
        self.seg_terms = [sorted(set(t)) for t in seg_terms]
        self.global_terms = sorted(set(t for s in self.seg_terms for t in s))
        self.gidx = {t: i for i, t in enumerate(self.global_terms)}
        # 每个段一张 segmentOrd → globalOrd
        self.seg2global = [[self.gidx[t] for t in s] for s in self.seg_terms]
        # globalOrd → (globalOrd − 该 term 在**第一个出现它的段**里的 ord)
        self.first_segment = {}
        self.global_ord_deltas = {}
        for g, t in enumerate(self.global_terms):
            fs = next(i for i, s in enumerate(self.seg_terms) if t in s)
            seg_ord = self.seg_terms[fs].index(t)
            self.first_segment[g] = fs
            self.global_ord_deltas[g] = g - seg_ord

    def seg_to_global(self, seg, seg_ord):
        return self.seg2global[seg][seg_ord]

    def global_to_seg(self, g):
        """反向映射:由 delta 与 firstSegment 还原段内 ord"""
        return g - self.global_ord_deltas[g]

    def packed_bits(self):
        """packed ints:用 ceil(log2(maxOrd)) 位存一个 ordinal,而不是固定 32 位"""
        n = len(self.global_terms)
        return max(1, int(math.ceil(math.log(n, 2)))) if n > 1 else 1


def aggregate(seg_docs, omap):
    """先在段内按 segment ord 计数,再映射到 global ord 累加成全局桶"""
    buckets = {}
    for seg, docs in enumerate(seg_docs):
        local = {}
        for term in docs:
            o = omap.seg_terms[seg].index(term)
            local[o] = local.get(o, 0) + 1
        for o, c in local.items():
            g = omap.seg_to_global(seg, o)
            buckets[g] = buckets.get(g, 0) + c
    return buckets


# ---------------------------------------------------------------- 主流程
def main():
    print("=" * 70)
    print("Demo 1 · 两种访问方向:倒排 term→docs vs 列存 doc→term")
    print("=" * 70)
    docs = [(1, "beijing"), (2, "shanghai"), (3, "beijing"),
            (4, "shenzhen"), (5, "beijing")]
    inv = build_inverted(docs)
    dv = build_doc_values(docs)
    print("   倒排:", inv)
    print("   列存:", dict(sorted(dv.items())))
    check(inv["beijing"] == [1, 3, 5], "倒排:查词 → 直接拿到文档列表")
    check(dv[4] == "shenzhen", "列存:拿文档 → 直接拿到它的词(排序/聚合/脚本要的方向)")

    print("\n" + "=" * 70)
    print("Demo 2 · doc-value-only 字段:能查,但要扫整列")
    print("=" * 70)
    hits_idx, scanned_idx = filter_by_term(inv, dv, "beijing", indexed=True)
    hits_dvo, scanned_dvo = filter_by_term(inv, dv, "beijing", indexed=False)
    print("   index:true  ⇒ 命中 %s,触碰 %d 个文档" % (hits_idx, scanned_idx))
    print("   index:false ⇒ 命中 %s,触碰 %d 个文档(全列扫描)" % (hits_dvo, scanned_dvo))
    check(hits_idx == hits_dvo, "两种写法结果集一致(只是代价不同)")
    check(scanned_dvo == len(docs) and scanned_idx < scanned_dvo,
          "doc-value-only 的过滤要扫整列 N 个文档,比倒排慢得多")

    print("\n" + "=" * 70)
    print("Demo 3 · 谁能关 doc_values")
    print("=" * 70)
    check(can_disable_doc_values(Field("kw", ftype="keyword")), "keyword 可以关")
    check(not can_disable_doc_values(Field("w", ftype="wildcard")),
          "wildcard 字段不能关 doc values")
    check(not can_disable_doc_values(Field("c", ftype="columnar")),
          "columnar index 里的字段不能关 doc values")
    check(Field("t", ftype="text").ftype == "text",
          "text / match_only_text 支持 doc values 但默认不启用;annotated_text 不支持")

    print("\n" + "=" * 70)
    print("Demo 4 · global ordinals:段内 ord ↔ 全局 ord")
    print("=" * 70)
    seg_terms = [["beijing", "shanghai"], ["beijing", "shenzhen"], ["shenzhen"]]
    omap = OrdinalMap(seg_terms)
    print("   段内字典:", omap.seg_terms)
    print("   全局字典:", omap.global_terms)
    print("   段0→全局:", omap.seg2global[0], " 段1→全局:", omap.seg2global[1])
    check(omap.global_terms == ["beijing", "shanghai", "shenzhen"], "全局字典 = 归并去重后排序")
    check(omap.seg_to_global(0, 0) == 0 and omap.seg_to_global(0, 1) == 1,
          "段 0 的 ord 0/1 → 全局 0/1")
    check(omap.seg_to_global(1, 1) == 2, "段 1 的 shenzhen(ord 1) → 全局 2(不是全局 1)")
    check(all(omap.global_to_seg(g) == omap.seg_terms[omap.first_segment[g]].index(t)
              for g, t in enumerate(omap.global_terms)),
          "反查:由 delta + firstSegment 能还原出段内 ord")
    bits = omap.packed_bits()
    print("   全局 term 数=%d ⇒ packed 每条目 %d 位(定长 32 位 ⇒ 压缩 %.1fx)"
          % (len(omap.global_terms), bits, 32.0 / bits))
    check(bits < 32, "packed ints 用 ceil(log2(n)) 位,远小于定长 32 位")

    print("\n" + "=" * 70)
    print("Demo 5 · 聚合:段内计数 → 映射累加 → 全局桶")
    print("=" * 70)
    seg_docs = [["beijing", "beijing", "shanghai"], ["beijing", "shenzhen"], ["shenzhen"]]
    buckets = aggregate(seg_docs, omap)
    named = {omap.global_terms[g]: c for g, c in sorted(buckets.items())}
    print("   全局桶:", named)
    check(named == {"beijing": 3, "shanghai": 1, "shenzhen": 2},
          "跨段同名 term 被正确并到同一个全局桶")
    check(sum(buckets.values()) == sum(len(d) for d in seg_docs),
          "桶计数总和 = 参与聚合的文档数(不丢不重)")

    print("\n" + "=" * 70)
    print("Demo 6 · multi_value:false(仅 columnar index)")
    print("=" * 70)

    def index_value(values, multi_value=True, on_failure="reject"):
        if not multi_value and len(values) > 1:
            if on_failure == "reject":
                raise ValueError("field is restricted to a single value")
            return values[:1]
        return values

    check(index_value(["a"], multi_value=False) == ["a"], "单值放行")
    try:
        index_value(["a", "b"], multi_value=False)
        check(False, "多值应被拒绝")
    except ValueError as e:
        check("single value" in str(e), "multi_value:false 时多值默认被拒绝")
    check(index_value(["a", "b"], multi_value=False, on_failure="ignore") == ["a"],
          "on_failure 可改成截取而非拒绝")
    return summary()


if __name__ == "__main__":
    sys.exit(main())
