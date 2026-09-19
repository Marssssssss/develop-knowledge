#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lucene / Elasticsearch bool 查询:匹配判定、评分合并与 minimum_should_match。

权威来源(实际读过,不凭记忆):
  1. https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-bool-query.html
     - 四类 occurrence:must(必须命中且**计分**) / should(可选,命中则加分) /
       filter(必须命中但**不计分**,走 filter context,**可被缓存**) /
       must_not(必须不命中,filter context,不计分)
     - "The bool query takes a more-matches-is-better approach":匹配的 must 与 should
       子句分数**相加**得到 _score
     - 纯 filter 的 bool ⇒ 全部文档 _score = 0;must: match_all + filter ⇒ 1.0;
       constant_score(filter) 与之**完全等效**
     - 嵌套:在 must 之下的 should **只提升分数,不新增文档**
  2. .../query-dsl-minimum-should-match.html
     - 默认值:含至少一个 should 且**没有 must 也没有 filter** ⇒ 1;否则 ⇒ 0
     - 规格:整数 / 负整数 / 百分比 / 负百分比 / 组合 `3<90%` / 多重组合 `2<-25% 9<-3`
     - 百分比**向下取整**;最终结果 clamp 到 [1, 可选子句数]
     - 4 子句时 75% 与 -25% 都是 3;5 子句时 75%⇒3 而 -25%⇒4
     - "若计算结果表明不需要可选子句,BooleanQuery 的通常规则仍然生效:
        不含 required 子句的 BooleanQuery 仍须匹配至少一个 optional 子句"
  3. .../analyzer-anatomy.html 之外无引用;BooleanQuery 本体:
     https://cdn.jsdelivr.net/gh/apache/lucene@main/lucene/core/src/java/org/apache/lucene/search/BooleanQuery.java
     (bool query 直接映射到 Lucene BooleanQuery)
"""
import math
import re
import sys

MUST, SHOULD, FILTER, MUST_NOT = "must", "should", "filter", "must_not"
SCORING = (MUST, SHOULD)          # 只有这两类参与计分
REQUIRED = (MUST, FILTER)         # 这两类是 required 子句

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


# ---------------------------------------------------------------- msm 规格
def _apply_one(spec, n):
    """把单个 spec 作用在可选子句数 n 上,返回必需的条数(未 clamp)"""
    spec = spec.strip()
    if spec.endswith("%"):
        v = float(spec[:-1])
        if v >= 0:
            return int(math.floor(n * v / 100.0))
        return n - int(math.floor(n * (-v) / 100.0))   # 负百分比:可缺失的比例
    v = int(spec)
    if v >= 0:
        return v
    return n + v                                        # 负整数:总数减去它


def parse_msm(spec, n_opt):
    """解析 minimum_should_match 规格串 → 必需的 should 条数。

    组合语法 `3<90%`:子句数 ≤ 3 时全部必需,超过则按 90% 算。
    多重组合 `2<-25% 9<-3`:各段只对**大于前一段阈值**的数量有效 ⇒
    取「阈值 < n 的最后一段」;没有匹配段时全部必需。
    """
    if n_opt == 0:
        return 0
    parts = spec.split()
    if len(parts) == 1 and "<" not in parts[0]:
        v = _apply_one(parts[0], n_opt)
    else:
        chosen = None
        for p in parts:
            if "<" in p:
                thr, tail = p.split("<", 1)
                if n_opt > int(thr):
                    chosen = tail
            else:
                chosen = p
        v = n_opt if chosen is None else _apply_one(chosen, n_opt)
    return max(1, min(v, n_opt))                        # clamp 到 [1, n_opt]


def default_msm(n_should, n_must, n_filter):
    """默认 minimum_should_match(官方默认值规则)"""
    if n_should >= 1 and n_must == 0 and n_filter == 0:
        return 1
    return 0


# ---------------------------------------------------------------- 模型
class Clause(object):
    def __init__(self, occur, name, hits, score_of=None):
        self.occur = occur
        self.name = name
        self.hits = set(hits)              # 命中该子句的文档 id
        self.score_of = score_of or {}     # doc -> 该子句贡献的分数

    def score(self, doc):
        return self.score_of.get(doc, 0.0)


class BoolQuery(object):
    def __init__(self, clauses, msm=None):
        self.clauses = list(clauses)
        self.msm = msm

    # ---- 匹配判定
    def effective_msm(self):
        cnt = {MUST: 0, SHOULD: 0, FILTER: 0, MUST_NOT: 0}
        for c in self.clauses:
            cnt[c.occur] += 1
        spec = self.msm if self.msm is not None else default_msm(
            cnt[SHOULD], cnt[MUST], cnt[FILTER])
        if isinstance(spec, int):
            base = spec
        else:
            base = parse_msm(spec, cnt[SHOULD])
        if cnt[MUST] == 0 and cnt[FILTER] == 0:
            # 无 required 子句 ⇒ 仍须匹配至少一个 optional 子句(文档明文)
            base = max(1, base) if cnt[SHOULD] else 0
        return base

    def matches(self, doc):
        for c in self.clauses:
            if c.occur in REQUIRED and doc not in c.hits:
                return False
            if c.occur == MUST_NOT and doc in c.hits:
                return False
        n_should_hit = sum(1 for c in self.clauses
                           if c.occur == SHOULD and doc in c.hits)
        return n_should_hit >= self.effective_msm()

    # ---- 评分:more-matches-is-better,匹配的 must/should 分数求和
    def score(self, doc):
        if not self.matches(doc):
            return 0.0
        return sum(c.score(doc) for c in self.clauses if c.occur in SCORING)


def search(q, docs):
    out = [(d, q.score(d)) for d in docs if q.matches(d)]
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


# ---------------------------------------------------------------- 主流程
def main():
    print("=" * 70)
    print("Demo 1 · 四类 occurrence 的匹配语义")
    print("=" * 70)
    docs = [1, 2, 3, 4]
    q = BoolQuery([
        Clause(MUST, "title:es", {1, 2, 3}, {1: 1.0, 2: 2.0, 3: 0.5}),
        Clause(FILTER, "status:active", {1, 2}),
        Clause(MUST_NOT, "tag:spam", {3}),
        Clause(SHOULD, "tag:new", {1, 4}, {1: 0.3, 4: 0.3}),
    ])
    hits = [d for d in docs if q.matches(d)]
    print("   命中:", hits)
    check(hits == [1, 2], "must∩filter 且排除 must_not ⇒ {1,2}")
    check(not q.matches(3), "doc3 命中 must_not ⇒ 被排除(即使它也命中 must)")
    check(not q.matches(4), "doc4 只命中 should,但存在 must/filter ⇒ 不会被 should 带进来")

    print("\n" + "=" * 70)
    print("Demo 2 · 评分:只有 must / should 计分,且是**求和**")
    print("=" * 70)
    print("   doc1=%.2f doc2=%.2f" % (q.score(1), q.score(2)))
    check(abs(q.score(1) - 1.3) < 1e-9, "doc1 = must 1.0 + should 0.3 = 1.3")
    check(abs(q.score(2) - 2.0) < 1e-9, "doc2 只命中 must(2.0),should 不加分")
    q2 = BoolQuery([
        Clause(SHOULD, "a", {1, 2}, {1: 1.0, 2: 1.0}),
        Clause(SHOULD, "b", {1}, {1: 2.0}),
    ])
    check(abs(q2.score(1) - 3.0) < 1e-9 and abs(q2.score(2) - 1.0) < 1e-9,
          "more-matches-is-better:命中 2 个 should 的 doc1 拿 3.0,只命中 1 个的 doc2 拿 1.0")

    print("\n" + "=" * 70)
    print("Demo 3 · filter context 不计分:三种写法的等价关系")
    print("=" * 70)
    f = Clause(FILTER, "status:active", {1, 2})
    only_filter = BoolQuery([Clause(MUST_NOT, "tag:spam", set()), f])
    must_all = BoolQuery([Clause(MUST, "match_all", {1, 2}, {1: 1.0, 2: 1.0}), f])
    print("   纯 filter      : %s" % [(d, only_filter.score(d)) for d in [1, 2]])
    print("   match_all+filter: %s" % [(d, must_all.score(d)) for d in [1, 2]])
    check([only_filter.score(d) for d in [1, 2]] == [0.0, 0.0],
          "纯 filter 的 bool ⇒ 所有命中文档 _score = 0")
    check([must_all.score(d) for d in [1, 2]] == [1.0, 1.0],
          "must: match_all + filter ⇒ 每个文档 1.0(constant_score 等效)")

    print("\n" + "=" * 70)
    print("Demo 4 · minimum_should_match 默认值")
    print("=" * 70)
    s_only = BoolQuery([Clause(SHOULD, "x", {1}), Clause(SHOULD, "y", {2})])
    s_with_must = BoolQuery([Clause(SHOULD, "x", {1}), Clause(MUST, "m", {1, 2})])
    s_with_filter = BoolQuery([Clause(SHOULD, "x", {1}), Clause(FILTER, "f", {1, 2})])
    print("   只有 should → msm=%d ; should+must → %d ; should+filter → %d"
          % (s_only.effective_msm(), s_with_must.effective_msm(), s_with_filter.effective_msm()))
    check(s_only.effective_msm() == 1, "只有 should 时默认 msm = 1")
    check(s_with_must.effective_msm() == 0, "有 must 时默认 msm = 0(should 退化为纯加分)")
    check(s_with_filter.effective_msm() == 0, "有 filter 时默认 msm = 0")
    check(s_with_must.matches(2), "msm=0 时,只命中 must 的文档也进结果集")
    check(s_only.matches(1) and s_only.matches(2), "msm=1 时,命中任一 should 即入选")
    none_required = BoolQuery([Clause(SHOULD, "x", {1}), Clause(SHOULD, "y", {2})], msm="0%")
    print("   显式 msm=0%% 但无 required 子句 → 生效值 %d" % none_required.effective_msm())
    check(none_required.effective_msm() == 1,
          "算出 0 也兜底成 1:无 required 子句时仍须匹配至少一个 optional 子句")

    print("\n" + "=" * 70)
    print("Demo 5 · msm 规格解析(百分比向下取整 + clamp)")
    print("=" * 70)
    check(parse_msm("3", 5) == 3, "整数 3 → 3")
    check(parse_msm("-2", 5) == 3, "负整数 -2 ⇒ 5-2 = 3")
    check(parse_msm("75%", 4) == 3 and parse_msm("-25%", 4) == 3,
          "4 子句时 75% 与 -25% 都是 3")
    check(parse_msm("75%", 5) == 3 and parse_msm("-25%", 5) == 4,
          "5 子句时 75%⇒3 而 -25%⇒4(官方 NOTE 的反直觉点)")
    check(parse_msm("3<90%", 3) == 3, "组合 3<90%:n=3 ≤ 3 ⇒ 全部必需")
    check(parse_msm("3<90%", 4) == 3, "组合 3<90%:n=4 ⇒ floor(3.6)=3")
    check(parse_msm("3<90%", 10) == 9, "组合 3<90%:n=10 ⇒ 9")
    check([parse_msm("2<-25% 9<-3", n) for n in (1, 2, 3, 9, 10, 12)] == [1, 2, 3, 7, 7, 9],
          "多重组合 2<-25% 9<-3 ⇒ n=1,2,3,9,10,12 → 1,2,3,7,7,9")
    check(parse_msm("999", 3) == 3 and parse_msm("-99", 3) == 1,
          "clamp:超出上限压到 n,低于下限抬到 1")

    print("\n" + "=" * 70)
    print("Demo 6 · 嵌套 bool:must 之下的 should 只加分,不新增文档")
    print("=" * 70)

    def clause_from(sub, occur, name):
        return Clause(occur, name,
                      {d for d in docs if sub.matches(d)},
                      {d: sub.score(d) for d in docs})

    inner = BoolQuery([Clause(SHOULD, "user:kimchy", {1}, {1: 1.0}),
                       Clause(SHOULD, "user:banon", {2}, {2: 1.0})])
    outer = BoolQuery([clause_from(inner, MUST, "nested-or"),
                       Clause(MUST, "tags:production", {1, 2, 3}, {3: 5.0})])
    print("   outer 命中:", [d for d in docs if outer.matches(d)])
    check(sorted(d for d in docs if outer.matches(d)) == [1, 2],
          "只有命中嵌套 OR 的文档入选(doc3 虽命中 tags 也不进来)")
    base = BoolQuery([Clause(MUST, "tags:production", {1, 2, 3}, {3: 5.0})])
    boosted = BoolQuery([Clause(MUST, "tags:production", {1, 2, 3}, {3: 5.0}),
                         Clause(SHOULD, "user:kimchy", {1}, {1: 1.0})])
    same_set = ([d for d in docs if base.matches(d)] ==
                [d for d in docs if boosted.matches(d)])
    check(same_set and boosted.score(1) > base.score(1),
          "顶层 should 只提升分数、不扩大结果集(doc1 加分但集合不变)")
    return summary()


if __name__ == "__main__":
    sys.exit(main())
