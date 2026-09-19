# -*- coding: utf-8 -*-
"""analyzer_chain 自检。从 analyzer_chain.py 拆出,内容逐字节搬运。"""
from analyzer_chain import *

# ---------------------------------------------------------------- 主流程
def selfcheck():
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
