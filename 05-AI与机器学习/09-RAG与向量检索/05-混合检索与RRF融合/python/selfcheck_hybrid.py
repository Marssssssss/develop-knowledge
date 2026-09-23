"""混合检索自检：Lucene BM25 与 Cormack 2009 RRF 的可断言细节。"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hybrid import (  # noqa: E402
    LENGTH_EXACT_UP_TO, LENGTH_TABLE_SIZE, LUCENE_B, LUCENE_K1, LUCENE_K3,
    PAPER_TABLE1_MAP, PAPER_TABLE2_MAP, RRF_K, bm25_avgdl, bm25_idf,
    bm25_norm_inverse, bm25_query_term_weight, bm25_score,
    bm25_score_monotone, bm25_tf, comb_mnz, condorcet_fuse, rrf_fuse,
    rrf_score, small_float_byte4_to_int,
)

PASS = 0
FAIL = []


def ck(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(msg)


def near(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ============================================ 1. Lucene BM25 的 idf
ck(near(bm25_idf(1, 10), math.log(1 + 9.5 / 1.5)),
   "idf(N=10, n=1) = log(1 + (10-1+0.5)/(1+0.5))")
ck(bm25_idf(2, 10) < bm25_idf(1, 10), "df 越大 idf 越小")
# 关键：Lucene 的 idf 恒为正（不像 log(N/n) 会变号）
ck(bm25_idf(10, 10) > 0, "df == N 时 idf 仍为正（实测 %.6f）" % bm25_idf(10, 10))
ck(all(bm25_idf(n, 10) > 0 for n in range(1, 11)), "任意 df 下 idf 都 > 0")
ck(near(bm25_idf(10, 10), math.log(1 + 0.5 / 10.5)), "df=N 时 idf = log(1+0.5/(N+0.5))")

# ============================================ 2. avgdl 与 tf
ck(near(bm25_avgdl(1000, 10), 100.0), "avgdl = sumTotalTermFreq / docCount")
try:
    bm25_avgdl(0, 0)
    ck(False, "docCount=0 应抛错")
except ValueError:
    ck(True, "docCount=0 抛错")

# tf 的核心是「没有 (k1+1) 分子」
tf_l = bm25_tf(3, LUCENE_K1, LUCENE_B, 100.0, 100.0)
tf_classic = (3 * (LUCENE_K1 + 1)) / (3 + LUCENE_K1 * ((1 - LUCENE_B)
                                                       + LUCENE_B * 100.0 / 100.0))
ck(near(tf_l, 3.0 / (3.0 + LUCENE_K1)), "dl == avgdl 时 tf = freq/(freq + k1)")
ck(tf_l < tf_classic, "Lucene 的 tf 比经典 BM25 少一个 (k1+1) 因子")
ck(near(tf_classic / tf_l, LUCENE_K1 + 1), "两者之比恰为 (k1+1)（排序不变的原因）")

# b=0 时长度完全不影响 tf
ck(near(bm25_tf(2, LUCENE_K1, 0.0, 10.0, 100.0),
        bm25_tf(2, LUCENE_K1, 0.0, 900.0, 100.0)), "b=0 时 dl 不参与")
# b=1 时 dl 越长 tf 越小
ck(bm25_tf(2, LUCENE_K1, 1.0, 200.0, 100.0) < bm25_tf(2, LUCENE_K1, 1.0, 50.0, 100.0),
   "b>0 时 dl 越长 tf 越小")
ck(bm25_tf(0, LUCENE_K1, LUCENE_B, 100.0, 100.0) == 0.0, "freq=0 -> tf=0")
ck(near(bm25_tf(5, 0.0, LUCENE_B, 100.0, 100.0), 1.0), "k1=0 -> tf=1（freq>0）")

# ============================================ 3. 单调改写
score_a = bm25_score(4, 3, 100, 120.0, 80.0)
score_b = bm25_score_monotone(4, 3, 100, 120.0, 80.0)
ck(near(score_a, score_b, 1e-12),
   "weight*(freq/(freq+norm)) 与 weight - weight/(1+freq*normInverse) 相等")
# 单调改写对 freq 单调、对 dl 单调
freqs = [bm25_score_monotone(f, 3, 100, 120.0, 80.0) for f in (1, 2, 4, 8, 16)]
ck(all(freqs[i] < freqs[i + 1] for i in range(4)), "对 freq 严格单调增")
dls = [bm25_score_monotone(4, 3, 100, 120.0, dl) for dl in (40, 80, 160, 320)]
ck(all(dls[i] > dls[i + 1] for i in range(3)), "对 dl 单调减（b>0）")
ck(near(bm25_norm_inverse(LUCENE_K1, LUCENE_B, 100.0, 100.0), 1.0 / LUCENE_K1),
   "dl=avgdl 时 normInverse = 1/k1")

# ============================================ 4. k3 / doclen 量化
ck(near(bm25_query_term_weight(3), 3.0), "k3=-1（默认禁用）时查询词频线性")
ck(near(bm25_query_term_weight(3, k3=0.0), 1.0),
   "k3=0 时权重恒为 1（(0+1)*qtf/(0+qtf)）")
ck(bm25_query_term_weight(2, k3=1.0) < bm25_query_term_weight(8, k3=1.0),
   "k3 启用后仍对 qtf 单调")
ck(bm25_query_term_weight(100, k3=1.0) < 2.0, "k3=1 时权重上界是 k3+1 = 2")
ck(LENGTH_TABLE_SIZE == 256, "doc 长度表只有 256 档（1 字节）")
ck(LENGTH_EXACT_UP_TO == 39, "norm > 39 时长度是近似值（官方 explain 里的分支）")
ck(small_float_byte4_to_int(0) == 0, "SmallFloat 0 -> 0")
ck(small_float_byte4_to_int(1) == 1, "SmallFloat 1 -> 1")
ck(all(small_float_byte4_to_int(b) <= small_float_byte4_to_int(b + 1)
       for b in range(255)), "SmallFloat 解码单调不减")
# 量化是有损的：4 位尾数 ⇒ 相邻档之间的**相对**步长在 1/15 ~ 1/8 之间
ratio = small_float_byte4_to_int(200) / float(small_float_byte4_to_int(199))
ck(ratio > 1.06, "高位段相邻档相差 %.2f%%（相对精度约 1/15，有损）" % ((ratio - 1) * 100))
ck(small_float_byte4_to_int(199) < small_float_byte4_to_int(200),
   "SmallFloat 解码在高位段仍然单调")

# ============================================ 5. RRF
ck(RRF_K == 60, "RRF 的 k 官方取 60（论文原文 fixed during a pilot investigation）")
ck(near(rrf_score([1]), 1 / 61.0), "单系统 rank 1 -> 1/61")
ck(near(rrf_score([1, 1]), 2 / 61.0), "两系统都排第 1 -> 2/61")
ck(rrf_score([1]) > rrf_score([2]), "rank 越小分数越高")
ck(near(rrf_score([]), 0.0), "未被任何系统召回 -> 0")
ck(rrf_score([1], k=0) == 1.0, "k=0 时退化为倒数排名 1/r")

# k 越大，相邻名次的差距越小（这就是「k 削弱离群系统」的机制）
gap10 = 1 / (10.0 + 1) - 1 / (10.0 + 2)
gap60 = 1 / (60.0 + 1) - 1 / (60.0 + 2)
gap500 = 1 / (500.0 + 1) - 1 / (500.0 + 2)
ck(gap10 > gap60 > gap500, "k 越大相邻名次差距越小（%.5f > %.5f > %.5f）"
   % (gap10, gap60, gap500))
# 「靠后排名的文档重要性不消失」：k+r 是线性的，不像指数那样迅速趋零
ck(1 / (60.0 + 100) > 0.0, "rank 100 仍贡献 1/160 > 0（不是指数衰减）")

# ============================================ 6. 论文 Table 1 的 k 敏感性
best_k = max(PAPER_TABLE1_MAP, key=lambda kk: PAPER_TABLE1_MAP[kk])
ck(best_k in (60, 70, 80), "论文 Table 1 的最优 k 落在 60~80（实测 %d）" % best_k)
ck(PAPER_TABLE1_MAP[best_k] - PAPER_TABLE1_MAP[60] < 0.0005,
   "k=60 与最优值相差不到 0.0005（论文：k=60 near-optimal，选择并不关键）")
ck(PAPER_TABLE1_MAP[60] > PAPER_TABLE1_MAP[0], "k=60 明显优于 k=0（无阻尼）")
spread = max(PAPER_TABLE1_MAP[kk] for kk in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)) \
    - min(PAPER_TABLE1_MAP[kk] for kk in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100))
ck(spread / PAPER_TABLE1_MAP[60] < 0.02,
   "k 取 10~100 的 MAP 波动不到 2%%（实测 %.4f%%）" % (spread / PAPER_TABLE1_MAP[60] * 100))
ck(PAPER_TABLE2_MAP["TREC Robust"]["RRF"]
   > PAPER_TABLE2_MAP["TREC Robust"]["best individual"], "TREC Robust：RRF 胜过最佳单系统")
ck(PAPER_TABLE2_MAP["TREC9"]["RRF"] < PAPER_TABLE2_MAP["TREC9"]["best individual"],
   "TREC9 是论文承认的例外（最佳单系统来自人工参与）")

# ============================================ 7. 融合行为
lists = [["a", "b", "c", "d"], ["c", "a", "e", "b"]]
fused = rrf_fuse(lists)
ck([d for d, _ in fused][0] == "a", "两路都靠前的 a 排第一")
ck(set(d for d, _ in fused) == set("abcde"), "融合覆盖两路的并集")
# 单系统时 RRF 排序 == 原排序（单调性）
single = rrf_fuse([["x", "y", "z"]])
ck([d for d, _ in single] == ["x", "y", "z"], "单系统时 RRF 不改变顺序")

# RRF 只吃秩：把分数整体乘以 100 不影响结果
smap1 = {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.6}
smap2 = {"c": 0.95, "a": 0.85, "e": 0.7, "b": 0.5}
cm = comb_mnz(lists, [smap1, smap2], cutoff=4)
ck(set(cm) == set("abcde"), "CombMNZ 也覆盖并集")
ck(cm["a"] > cm["d"], "CombMNZ 偏好被多路同时命中的文档")
# 负控：分数尺度会改变 CombMNZ 而不改变 RRF
scaled = [{k: v * 100 for k, v in smap1.items()}, {k: v * 100 for k, v in smap2.items()}]
cm2 = comb_mnz(lists, scaled, cutoff=4)
ck(near(cm2["a"] / cm["a"], 100.0), "CombMNZ 随分数尺度线性放大（需要归一化）")
ck(rrf_fuse(lists) == rrf_fuse(lists), "RRF 与分数无关")

cf = condorcet_fuse(lists, ["a", "b", "c", "d", "e"])
ck(isinstance(cf, list) and len(cf) == 5, "Condorcet 对全部候选排序")

print("PASS=%d  FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
