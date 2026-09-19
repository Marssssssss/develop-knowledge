#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ES/Lucene 分析器链与中文分词 —— 纯标准库最小模型。

权威来源(实际读过,不凭记忆):
  1. https://www.elastic.co/guide/en/elasticsearch/reference/current/analyzer-anatomy.html
     - analyzer 由 3 类构件打包:character filters / tokenizer / token filters
     - char filter 0+ 个,**按顺序**作用;tokenizer **恰好 1 个**;token filter 0+ 个,按顺序
     - tokenizer 同时负责记录每个 term 的 **position** 与字符 **offset**
     - **token filter 不允许改变每个 token 的 position 与 offset**
  2. .../analysis.html —— tokenization(切成 token) + normalization(归一化)
  3. .../analysis-analyzers.html —— standard analyzer 按 Unicode 文本切分算法在词边界
     切分,去掉大部分标点、小写化、支持停用词
  4. .../index-modules-similarity.html —— BM25: k1 默认 1.2、b 默认 0.75;
     discount_overlaps 默认 **true**:position increment 为 0 的 **overlap token 不计入 norm**
  5. .../norms.html —— norm 大约占 **每文档每字段 1 字节**,不打分就该关掉
  6. https://cdn.jsdelivr.net/gh/infinilabs/analysis-ik@master/core/src/main/java/org/wltea/analyzer/core/IKSegmenter.java
     - 4 个子分词器:Letter / SurrogatePair / CN_Quantifier / CJKSegmenter
     - 歧义裁决:`this.arbitrator.process(context, configuration.isUseSmart())`
       ⇒ 智能/粗粒度与否是**同一套词元的后处理开关**,不是两套词典
"""
import math
import re
import sys

K1 = 1.2          # index-modules-similarity: BM25 k1 默认 1.2
B = 0.75          # index-modules-similarity: BM25 b 默认 0.75
NORMS_BYTES_PER_DOC_FIELD = 1   # norms.html: ~1 byte per document per field

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


def ui(v):
    """把浮点收敛到友好精度,避免 1e-17 级别的噪声干扰断言可读性"""
    return round(v + 0.0, 6)


# ---------------------------------------------------------------- 构件
class Token(object):
    __slots__ = ("term", "start", "end", "pos", "pos_inc")

    def __init__(self, term, start, end, pos, pos_inc):
        self.term = term
        self.start = start
        self.end = end
        self.pos = pos
        self.pos_inc = pos_inc

    def __repr__(self):
        return "Token(%r,%d,%d,pos=%d,inc=%d)" % (
            self.term, self.start, self.end, self.pos, self.pos_inc)


def cf_html_strip(text):
    """字符过滤器:剥掉 HTML 标签(0+ 个,最先作用)"""
    return re.sub(r"<[^>]+>", " ", text)


def cf_mapping(text, table):
    """字符过滤器:字符映射(在 html_strip 之后作用,顺序敏感)"""
    for a, b in table:
        text = text.replace(a, b)
    return text


def tk_standard(text):
    """分词器(恰好 1 个):按字母/数字连续段切分,记录 offset 与 position。

    官方文档对 standard tokenizer 的描述是「Unicode 文本切分算法 + 去大部分标点」;
    这里退化为「字母数字连续段」,目的是为了**可以离线验证 offset/position 的正确性**。
    """
    out = []
    pos = 0
    for m in re.finditer(r"[0-9A-Za-z]+", text):
        # Lucene 的 positionIncrement 默认就是 1(首个 token 也是 1,不是 0)
        out.append(Token(m.group(0), m.start(), m.end(), pos, 1))
        pos += 1
    return out


def tf_lowercase(tokens):
    """token filter:小写化(归一化)。不得改 position / offset。"""
    return [Token(t.term.lower(), t.start, t.end, t.pos, t.pos_inc) for t in tokens]


def tf_stop(tokens, stops):
    """token filter:去停用词。

    按文档「token filter 不允许改变 token 的 position」的规则:被删掉的 token 留下
    **位置空洞**,存活 token 的 position 原样保留(不重排号)。
    """
    return [t for t in tokens if t.term not in stops]


def tf_synonym(tokens, syn):
    """token filter:同义词展开。

    展开出来的词与原词**同占一个 position** ⇒ positionIncrement = 0,
    即 similarity 文档里说的 overlap token(discount_overlaps 会把它们排除出 norm)。
    """
    out = []
    for t in tokens:
        out.append(t)
        for s in syn.get(t.term, []):
            out.append(Token(s, t.start, t.end, t.pos, 0))
    return out


class Analyzer(object):
    def __init__(self, name, char_filters=None, tokenizer=None, token_filters=None):
        if tokenizer is None:
            raise ValueError("an analyzer must have exactly one tokenizer")
        self.name = name
        self.char_filters = char_filters or []
        self.tokenizer = tokenizer
        self.token_filters = token_filters or []

    def analyze(self, text):
        for cf in self.char_filters:
            text = cf(text)
        toks = self.tokenizer(text)
        for f in self.token_filters:
            toks = f(toks)
        return toks


# ---------------------------------------------------------------- 中文分词
IK_DICT = ["中华人民共和国", "人民共和国", "中华", "人民", "共和国",
           "共和", "国", "成立", "了", "万岁"]


def _reachable(s):
    """suffix 可切分性 DP:reachable[i] = s[i:] 能否被词典完全覆盖"""
    n = len(s)
    r = [False] * (n + 1)
    r[n] = True
    for i in range(n - 1, -1, -1):
        for w in IK_DICT:
            if s.startswith(w, i) and r[i + len(w)]:
                r[i] = True
                break
    return r


def ik_candidates(s):
    """ik_max_word:输出**所有能参与某种完整切分**的词元(细粒度、彼此重叠)"""
    r = _reachable(s)
    out = []
    for i in range(len(s)):
        for w in IK_DICT:
            if s.startswith(w, i) and r[i + len(w)]:
                out.append((i, w))
    return out


def ik_smart_path(s):
    """ik_smart:最长匹配 greedy,只留一条路径(粗粒度)"""
    out = []
    i = 0
    while i < len(s):
        best = None
        for w in IK_DICT:
            if s.startswith(w, i) and (best is None or len(w) > len(best)):
                best = w
        if best is None:
            best = s[i]          # 未登录单字兜底
        out.append((i, best))
        i += len(best)
    return out


def to_tokens(pairs, overlap_are_synonyms):
    """把 (offset, word) 列表变成 token 流。

    modeling note(建模声明,非官方原文):IK 源码只暴露 `isUseSmart()` 这一裁决开关,
    没有规定重叠词元的 positionIncrement。本 demo 按 Lucene 通用约定把「不在主路径上的
    重叠词元」标成 pos_inc=0,正好对应 discount_overlaps 的处理对象。
    """
    toks = []
    pos = -1
    prev_end = None
    for off, w in pairs:
        if prev_end is not None and off >= prev_end:
            pos += 1
            inc = 1 if off > prev_end else 0
        else:
            inc = 0
        if overlap_are_synonyms and prev_end is not None and off < prev_end:
            inc = 0
        toks.append(Token(w, off, off + len(w), max(pos, 0), inc))
        prev_end = max(prev_end or 0, off + len(w))
    return toks


# ---------------------------------------------------------------- BM25
def bm25(tf, dl, avgdl, n, df, k1=K1, b=B):
    """BM25 单项评分。idf 用 Lucene 的概率版(含 +0.5 平滑)"""
    idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
    denom = tf + k1 * (1.0 - b + b * dl / avgdl)
    return idf * (tf * (k1 + 1.0)) / denom


def norm_length(tokens, discount_overlaps=True):
    """字段长度(参与 norm 的 token 数)。discount_overlaps=True 时忽略 pos_inc==0 的词"""
    if discount_overlaps:
        return sum(1 for t in tokens if t.pos_inc != 0)
    return len(tokens)


# ---------------------------------------------------------------- 主流程
def main():
    print("=" * 70)
    print("Demo 1 · analyzer 的三段式结构:char filter(0+) → tokenizer(1) → token filter(0+)")
    print("=" * 70)
    std = Analyzer(
        "english",
        char_filters=[cf_html_strip],
        tokenizer=tk_standard,
        token_filters=[
            tf_lowercase,
            lambda ts: tf_stop(ts, {"the", "a", "is"}),
            lambda ts: tf_synonym(ts, {"quick": ["fast"]}),
        ])
    toks = std.analyze("<b>The</b> Quick brown-fox")
    for t in toks:
        print("   %r  offset=[%d,%d)  pos=%d  posInc=%d"
              % (t.term, t.start, t.end, t.pos, t.pos_inc))
    check(len(toks) >= 4, "html_strip 先跑,所以标签里的 'b' 不会成为 token")
    check(all(t.term == t.term.lower() for t in toks), "lowercase 生效")
    check("the" not in [t.term for t in toks], "停用词 the 被移除")
    check(any(t.term == "fast" for t in toks), "同义词 quick→fast 展开")

    print("\n" + "=" * 70)
    print("Demo 2 · token filter 不允许改 position / offset(文档明文规则)")
    print("=" * 70)
    raw = tk_standard("The Quick brown fox")
    kept = tf_lowercase(raw)
    check([(t.start, t.end) for t in raw] == [(t.start, t.end) for t in kept],
          "lowercase 后 offset 逐个不变")
    check([t.pos for t in raw] == [t.pos for t in kept], "lowercase 后 position 逐个不变")
    dropped = tf_stop(kept, {"the"})
    check([t.pos for t in dropped] == [1, 2, 3],
          "停用词留下位置空洞:存活 token 的 position 仍是 1,2,3(没有重排号)")
    syn = tf_synonym(kept, {"quick": ["fast"]})
    fast = [t for t in syn if t.term == "fast"][0]
    check(fast.pos_inc == 0 and fast.start == kept[1].start,
          "同义词与原词同占一个 position ⇒ posInc=0 且 offset 复用")

    print("\n" + "=" * 70)
    print("Demo 3 · 中文分词:ik_max_word(细粒度/重叠) vs ik_smart(粗粒度/单路径)")
    print("=" * 70)
    s = "中华人民共和国成立了"
    cand = ik_candidates(s)
    path = ik_smart_path(s)
    print("   ik_max_word : " + " / ".join(w for _, w in cand))
    print("   ik_smart    : " + " / ".join(w for _, w in path))
    check(len(cand) > len(path), "max_word 的词元数多于 smart")
    check([w for _, w in path] == ["中华人民共和国", "成立", "了"],
          "smart 走最长匹配单路径")
    check(("中华人民共和国" in [w for _, w in cand]) and ("中华" in [w for _, w in cand]),
          "max_word 同时给出整词与子串(重叠)")

    print("\n" + "=" * 70)
    print("Demo 4 · 分词粒度改变 dl ⇒ 改变 norm ⇒ 改变 BM25 分数")
    print("=" * 70)
    docs = ["中华人民共和国成立了", "中华人民共和国万岁"]
    dls_smart = [len(ik_smart_path(d)) for d in docs]
    dls_max = [len(ik_candidates(d)) for d in docs]
    avg_s = sum(dls_smart) / float(len(dls_smart))
    avg_m = sum(dls_max) / float(len(dls_max))
    s_smart = bm25(1, dls_smart[0], avg_s, n=2, df=1)
    s_max = bm25(1, dls_max[0], avg_m, n=2, df=1)
    print("   smart    粒度 dl=%s avgdl=%.2f score=%.6f" % (dls_smart, avg_s, s_smart))
    print("   max_word 粒度 dl=%s avgdl=%.2f score=%.6f" % (dls_max, avg_m, s_max))
    check(s_smart != s_max, "分词粒度不同 ⇒ dl/avgdl 不同 ⇒ BM25 分数不同(索引期与查询期必须用同一 analyzer)")

    # 长度归一化本身:b 控制「文档长度多大程度压制 tf」
    short = bm25(1, 3, 6.0, n=10, df=2)
    long_ = bm25(1, 9, 6.0, n=10, df=2)
    print("   固定 avgdl=6: dl=3 → %.6f ; dl=9 → %.6f" % (short, long_))
    check(short > long_, "b=0.75 下,更短的文档拿到更高的 tf 分量(长度归一化)")
    check(ui(bm25(1, 3, 6.0, n=10, df=2, b=0.0)) == ui(bm25(1, 9, 6.0, n=10, df=2, b=0.0)),
          "b=0 时长度归一化完全关闭 ⇒ dl=3 与 dl=9 得分相同")

    print("\n" + "=" * 70)
    print("Demo 5 · discount_overlaps:norm 是否把 posInc=0 的重叠词算进长度")
    print("=" * 70)
    t_syn = tf_synonym(tk_standard("quick fox"), {"quick": ["fast"]})
    n_on = norm_length(t_syn, True)
    n_off = norm_length(t_syn, False)
    print("   token 数=%d  discount_overlaps=true→%d  false→%d" % (len(t_syn), n_on, n_off))
    check(n_on == 2 and n_off == 3,
          "默认 true:0-increment 的 fast 不计入 norm(长度仍是 2)")
    base = [t for t in t_syn if t.pos_inc != 0]
    d1 = norm_length(base, True)
    d2 = norm_length(base, False)
    check(d1 == d2, "没有重叠词时开关不影响长度 ⇒ 差异只来自 overlap token")
    print("   norm 存储成本 ≈ %d 字节/文档/字段(norms.html)"
          % NORMS_BYTES_PER_DOC_FIELD)
    return summary()


if __name__ == "__main__":
    sys.exit(main())
