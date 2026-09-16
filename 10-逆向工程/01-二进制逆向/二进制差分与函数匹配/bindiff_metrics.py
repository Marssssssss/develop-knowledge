#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BinDiff 的置信度压扁(confidence)与相似度权重(similarity)。

依据(本轮实读的官方 concepts.md 原文):
  * confidence:『The confidence value displayed by BinDiff is the average algorithm
    confidence (match quality) used to find a particular match weighted by a sigmoid
    squashing function. The values aren't simply averaged because few single weak matches
    in an otherwise perfectly matched function/binary shouldn't drag the confidence down
    too much. Analogously, even a few strong matches will not "rescue" a binary pair
    matched primarily by address sequence and similarly weak algorithms.』
    即:先取均值,再用 S 型函数压扁 —— 这就是本模块 squashed_confidence 的实现。
  * function similarity 权重:flow graph 边 25% / 基本块 15% / 指令 10% /
    flow graph MD index 差异 50%。
  * binary similarity 权重:边 35% / 基本块 25% / 函数 10% / 指令 10% /
    call graph MD index 差异 20%,且『Only non-library functions are considered for
    the counts. This is to avoid inflating the similarity of binaries that simply use
    the same runtime library but are otherwise completely dissimilar.』
  * 两者最后都**再乘 confidence**:『even a seemingly good match is not trustworthy
    if produced by weak algorithms』。
  * **口径差异(如实标注)**:官方对各算法只给**定性等级**(very good / good /
    medium / poor / very poor),并没有公开数值;本 demo 把等级映射成数值
    (0.98 / 0.80 / 0.60 / 0.45)只是为了让流水线可跑、可断言,不代表官方取值。

运行: python bindiff_metrics.py     退出码 0 表示全部断言通过。
"""

import math
import sys

FAIL = []


def check(cond, label, detail=""):
    if not cond:
        FAIL.append(label)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  <- " + str(detail)) if detail else ""))
    return cond


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def normalize_sigmoid_cdf(m, k=8.0):
    """把 [0,1] 上的均值经 S 型函数映射回 [0,1],两端饱和(官方"压扁"的效果)。

    k 控制陡峭程度:k 越大,越接近"要么接近 0、要么接近 1"的硬判决。
    """
    lo, hi = sigmoid(-k / 2.0), sigmoid(k / 2.0)
    return (sigmoid(k * (m - 0.5)) - lo) / (hi - lo)


def squashed_confidence(confs, k=8.0):
    """confidence = 各算法置信度的均值经 S 型压扁。"""
    return normalize_sigmoid_cdf(sum(confs) / len(confs), k)


def function_similarity(edges_ratio, blocks_ratio, insns_ratio, md_distance, conf):
    """官方权重:边 25% / 块 15% / 指令 10% / flow graph MD index 差异 50%,最后乘 confidence。"""
    s = (0.25 * edges_ratio + 0.15 * blocks_ratio + 0.10 * insns_ratio +
         0.50 * (1.0 - md_distance))
    return s * conf


def binary_similarity(edges_ratio, blocks_ratio, funcs_ratio, insns_ratio, cg_distance, conf):
    """官方权重:边 35% / 块 25% / 函数 10% / 指令 10% / call graph MD index 差异 20%。"""
    s = (0.35 * edges_ratio + 0.25 * blocks_ratio + 0.10 * funcs_ratio +
         0.10 * insns_ratio + 0.20 * (1.0 - cg_distance))
    return s * conf


def main():
    print("== 6. confidence:为什么不是简单平均 ==")
    strong = [0.95] * 9 + [0.10]
    d_sq = abs(squashed_confidence(strong) - normalize_sigmoid_cdf(0.95))
    d_lin = abs(sum(strong) / len(strong) - 0.95)
    check(d_sq < d_lin, "整体很强时,一个弱匹配对压扁值的影响小于对线性均值的影响",
          "squashed Δ=%.4f < linear Δ=%.4f" % (d_sq, d_lin))
    weak = [0.20] * 9 + [0.90]
    check(squashed_confidence(weak) < 0.30,
          "整体很弱时,一个强匹配也救不起来(仍在弱区间)",
          "%.4f" % squashed_confidence(weak))
    check(abs(normalize_sigmoid_cdf(0.0)) < 1e-12 and
          abs(normalize_sigmoid_cdf(1.0) - 1.0) < 1e-12,
          "归一化 S 型把 [0,1] 映射回 [0,1]")
    check(abs(squashed_confidence([0.5]) - 0.5) < 1e-12,
          "单元素时压扁退化为恒等映射(0.5 → 0.5)")
    check(squashed_confidence([0.9, 0.9, 0.9]) > squashed_confidence([0.9, 0.5, 0.5]),
          "同样是 3 条匹配,整体一致的比参差不齐的更可信")

    print("== 7. similarity:权重与「乘 confidence」 ==")
    check(abs(function_similarity(1.0, 1.0, 1.0, 0.0, 1.0) - 1.0) < 1e-12,
          "四项全满 + conf=1 → similarity = 1")
    check(abs(function_similarity(1.0, 1.0, 1.0, 0.0, 0.5) - 0.5) < 1e-12,
          "confidence 直接相乘:conf 减半 → similarity 减半")
    check(abs(function_similarity(0.0, 0.0, 0.0, 1.0, 1.0)) < 1e-12,
          "MD index 差异权重 50% 全失配 → similarity = 0(单靠 MD 差异就占一半)")
    check(abs(binary_similarity(1.0, 1.0, 1.0, 1.0, 0.0, 1.0) - 1.0) < 1e-12,
          "binary similarity 权重和 = 1(0.35+0.25+0.10+0.10+0.20)")
    check(abs(binary_similarity(1.0, 1.0, 1.0, 1.0, 1.0, 0.9) - 0.72) < 1e-12,
          "call graph MD 差异 20% 全失配,再乘 conf=0.9 → 0.8*0.9 = 0.72")
    check(0.25 + 0.15 + 0.10 + 0.50 == 1.0 and 0.35 + 0.25 + 0.10 + 0.10 + 0.20 == 1.0,
          "两套权重各自归一")

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
