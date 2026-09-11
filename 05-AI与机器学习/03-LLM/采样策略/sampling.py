#!/usr/bin/env python3
"""LLM 解码采样策略：greedy / temperature / top-k / top-p (nucleus)。

依据 HuggingFace 官方博客《How to generate text》(Patrick von Platen)：
自回归生成 P(w_1:T | W_0) = prod P(w_t | w_1:t-1, W_0)，每步从模型输出的
logits 分布中选出下一个 token。贪心/波束搜索确定性但易重复；采样类策略
通过 temperature 重缩放与 top-k / top-p 截断控制随机性。

四个 demo：
  1. temperature：T 扫描下的分布形态与熵，T->0 退化为 greedy
  2. top-k：固定候选数截断，尖峰/平坦分布行为对比
  3. top-p (nucleus)：按累积概率质量动态确定候选数
  4. 采样频率近似概率：多次采样统计验证实现正确性

纯标准库，无第三方依赖。
"""

from __future__ import annotations

import math
import random


def softmax(logits: list[float]) -> list[float]:
    """数值稳定的 softmax：先减 max 防止 exp 溢出。"""
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    total = sum(exps)
    return [e / total for e in exps]


def with_temperature(logits: list[float], t: float) -> list[float]:
    """温度缩放：p_i = softmax(z_i / T)。

    T < 1 分布更尖（趋向 greedy）；T > 1 分布更平（更随机）；
    T -> 0 极限即贪心解码（HF 官方博客：temperature=0 等价 greedy）。
    """
    return softmax([x / t for x in logits])


def top_k_filter(probs: list[float], k: int) -> list[float]:
    """Top-K：只保留概率最高的 k 个候选，其余置 0 后重归一化。

    HF 文档：top_k 常用范围 5-50；至少保留 1 个。
    """
    k = max(1, min(k, len(probs)))
    order = sorted(range(len(probs)), key=lambda i: -probs[i])
    keep = set(order[:k])
    filtered = [p if i in keep else 0.0 for i, p in enumerate(probs)]
    total = sum(filtered)
    return [p / total for p in filtered]


def top_p_filter(probs: list[float], p: float) -> list[float]:
    """Top-P / 核采样：按概率降序累积，保留覆盖 p 概率质量的最小集合。

    候选数是动态的：分布尖峰时候选少，平坦时候选多（HF 博客核心论点）。
    HF 实现约定 min_tokens_to_keep=1 —— 累积已超 p 也要至少保留首 token。
    """
    order = sorted(range(len(probs)), key=lambda i: -probs[i])
    filtered = [0.0] * len(probs)
    cum = 0.0
    for idx in order:
        filtered[idx] = probs[idx]
        cum += probs[idx]
        if cum >= p:
            break
    total = sum(filtered)
    return [x / total for x in filtered]


def entropy(probs: list[float]) -> float:
    """香农熵（bit），衡量分布的不确定度。"""
    return -sum(p * math.log2(p) for p in probs if p > 0)


def draw(probs: list[float], rng: random.Random) -> int:
    """从离散分布中采样一个下标。"""
    r = rng.random()
    cum = 0.0
    for i, p in enumerate(probs):
        cum += p
        if r < cum:
            return i
    return len(probs) - 1


def demo1_temperature():
    print("=" * 62)
    print("Demo 1: temperature 对下一 token 分布的影响")
    print("=" * 62)
    logits = [2.0, 1.0, 0.5, -1.0, -2.0]
    tokens = ["the", "a", "one", "some", "any"]
    greedy_pick = tokens[max(range(len(logits)), key=lambda i: logits[i])]
    print(f"原始 logits = {logits}（候选 {tokens}）")
    for t in [0.1, 0.5, 1.0, 5.0]:
        probs = with_temperature(logits, t)
        bar = " ".join(f"{p:.2f}" for p in probs)
        print(f"T={t:<4} 熵={entropy(probs):.2f} bit  分布=[{bar}]")
    tiny = with_temperature(logits, 1e-4)
    pick = tiny.index(max(tiny))
    print(f"T->0 时分布集中在 '{tokens[pick]}'，与 greedy 结果 "
          f"'{greedy_pick}' 一致: {tokens[pick] == greedy_pick}")
    print("-> T 越小越确定（代码任务推荐低温），越大越多样（开放生成）")


def demo2_top_k():
    print()
    print("=" * 62)
    print("Demo 2: top-k 截断（固定候选数 k=2）")
    print("=" * 62)
    peaked = softmax([5.0, 1.0, 1.0, 1.0, 1.0])   # 尖峰分布
    flat = softmax([0.0, 0.0, 0.0, 0.0, 0.0])     # 完全平坦
    for name, probs in [("尖峰", peaked), ("平坦", flat)]:
        k2 = top_k_filter(probs, 2)
        kept = [i for i, p in enumerate(k2) if p > 0]
        print(f"{name}分布 top-2 保留候选 {kept}，重归一化后 "
              f"{[round(p, 3) for p in k2]}")
    print("-> top-k 固定保留 k 个：尖峰时仍带着低质量长尾候选；")
    print("   平坦时又可能过早砍掉合理候选 —— 缺点是候选数不随分布自适应")


def demo3_top_p():
    print()
    print("=" * 62)
    print("Demo 3: top-p / 核采样（p=0.9，候选数动态）")
    print("=" * 62)
    peaked = softmax([6.0, 1.0, 1.0, 1.0, 1.0])
    medium = softmax([2.0, 1.5, 1.0, 0.5, 0.0])
    flat = softmax([0.0, 0.0, 0.0, 0.0, 0.0])
    for name, probs in [("尖峰", peaked), ("中等", medium), ("平坦", flat)]:
        out = top_p_filter(probs, 0.9)
        n_kept = sum(1 for p in out if p > 0)
        print(f"{name}分布 -> 核采样保留 {n_kept} 个候选 "
              f"{[round(p, 3) for p in out if p > 0]}")
    print("-> 同一个 p=0.9：尖峰只留 1-2 个、平坦留 5 个 —— 候选数自适应，")
    print("   这是 HF 博客推荐 top-p 替代 top-k 的原因")


def demo4_frequency():
    print()
    print("=" * 62)
    print("Demo 4: 采样频率近似概率（T=0.8 + top_p=0.9, 20000 次）")
    print("=" * 62)
    logits = [2.0, 1.5, 1.0, 0.0, -0.5]
    probs = top_p_filter(with_temperature(logits, 0.8), 0.9)
    rng = random.Random(123)
    n = 20000
    counts = [0] * len(probs)
    for _ in range(n):
        counts[draw(probs, rng)] += 1
    print(f"{'idx':>3} {'理论概率':>8} {'采样频率':>8} {'偏差':>7}")
    for i, (p, c) in enumerate(zip(probs, counts)):
        freq = c / n
        print(f"{i:>3} {p:>8.4f} {freq:>8.4f} {abs(freq - p):>7.4f}")
    print("-> 大数定律：频率收敛于截断重归一化后的理论分布，验证管线正确")


def main():
    demo1_temperature()
    demo2_top_k()
    demo3_top_p()
    demo4_frequency()
    print()
    print("全部 demo 完成。")


if __name__ == "__main__":
    main()
