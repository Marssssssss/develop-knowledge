#!/usr/bin/env python3
"""BPE (Byte Pair Encoding) 分词器最小实现。

依据 Sennrich et al. 2016 (arXiv:1508.07909)：把数据压缩算法 BPE 改造为
子词分段算法，使固定词表的 NMT 模型具备开放词表能力。

三个 demo：
  1. 经典语料训练：逐步打印每轮"最高频相邻符号对"合并过程
  2. 编码未见词：按 merge 学习顺序（rank 优先）贪心应用规则
  3. 词表大小权衡：扫描 merge 次数，观察 token 数与词表大小的此消彼长

纯标准库，无第三方依赖。
"""

from __future__ import annotations

import collections

# 词尾标记：Sennrich 2016 用 </w> 区分"词内字符"与"词边界"，
# 让模型能区分 "est" 在词尾 (est</w>) 与词中 (est...)。
WORD_END = "</w>"


def train_bpe(word_freq: dict[str, int], num_merges: int):
    """在词频表上训练 BPE，返回有序 merge 规则表。

    :param word_freq: {单词: 出现次数}
    :param num_merges: 合并轮数（每轮产生 1 条新规则、词表 +1）
    :return: (merges, vocab) — merges 为 [(left, right), ...] 按学习顺序；
             vocab 为当前所有符号的集合
    """
    # 1) Seed：把每个词拆成单字符序列（末尾加词尾标记）
    words = {tuple(list(w) + [WORD_END]): c for w, c in word_freq.items()}
    vocab = set(ch for syms in words for ch in syms)
    merges: list[tuple[str, str]] = []

    for _ in range(num_merges):
        # 2) Count：跨语料统计相邻符号对（按词频加权）
        pair_counts: dict[tuple[str, str], int] = collections.Counter()
        for syms, freq in words.items():
            for i in range(len(syms) - 1):
                pair_counts[(syms[i], syms[i + 1])] += freq
        if not pair_counts:
            break
        best = max(pair_counts.items(), key=lambda kv: (kv[1], kv[0]))
        if best[1] < 2:  # 只出现 1 次的对没有合并价值
            break
        # 3) Merge：把最高频对替换为合并后的新符号
        left, right = best[0]
        new_sym = left + right
        merges.append((left, right))
        vocab.add(new_sym)
        # 3) Merge：把最高频对替换为合并后的新符号（所有词重写一遍）
        words = _apply_merge_all(words, left, right)
    return merges, vocab


def _apply_merge_all(words, left, right):
    """对每个词符号序列应用一次合并规则（从左到右贪心替换）。"""
    new_sym = left + right
    out: dict[tuple, int] = {}
    for syms, freq in words.items():
        res = []
        i = 0
        while i < len(syms):
            if i < len(syms) - 1 and syms[i] == left and syms[i + 1] == right:
                res.append(new_sym)
                i += 2
            else:
                res.append(syms[i])
                i += 1
        key = tuple(res)
        out[key] = out.get(key, 0) + freq
    return out


def encode_word(word: str, merges: list[tuple[str, str]]) -> list[str]:
    """用已训练的 merge 规则编码新词。

    关键：不是按"新文本里的频率"合并，而是按规则的学习顺序（rank）——
    每步在当前符号序列中找 rank 最小（最早学到）的可应用规则并应用，
    从而保证同一份规则表对任何输入产生唯一确定的切分。
    """
    rank = {pair: i for i, pair in enumerate(merges)}
    syms = list(word) + [WORD_END]
    while True:
        # 找当前序列中 rank 最小的相邻对
        best_i, best_rank = -1, None
        for i in range(len(syms) - 1):
            r = rank.get((syms[i], syms[i + 1]))
            if r is not None and (best_rank is None or r < best_rank):
                best_i, best_rank = i, r
        if best_i < 0:
            return syms
        left, right = syms[best_i], syms[best_i + 1]
        syms[best_i:best_i + 2] = [left + right]


def demo1_classic_training():
    """Sennrich 2016 论文中的经典语料，逐步观察合并过程。"""
    print("=" * 62)
    print("Demo 1: 经典语料训练（low/lower/newest/widest）")
    print("=" * 62)
    corpus = {
        "low": 5,
        "lower": 2,
        "newest": 6,
        "widest": 3,
    }
    # 论文表 1：第一轮 (e,s) 出现 9 次（newest×6 + widest×3）
    words = {tuple(list(w) + [WORD_END]): c for w, c in corpus.items()}
    for rnd in range(6):
        pair_counts = collections.Counter()
        for syms, freq in words.items():
            for i in range(len(syms) - 1):
                pair_counts[(syms[i], syms[i + 1])] += freq
        best = max(pair_counts.items(), key=lambda kv: (kv[1], kv[0]))
        print(f"第 {rnd + 1} 轮: 最高频对 {best[0]} = {best[1]} 次 -> 合并为 "
              f"'{best[0][0] + best[0][1]}'")
        words = _apply_merge_all(words, best[0][0], best[0][1])
        # 打印合并后每个词的符号序列
        for syms, freq in sorted(words.items(), key=lambda kv: -kv[1]):
            print(f"    {freq:>2} x {' '.join(syms)}")
    print("-> 词表 = 初始字符 + 每轮合并出的新符号；常见组合逐渐变成单 token")


def demo2_encode_unseen():
    """编码训练语料中没有的词，展示子词泛化能力。"""
    print()
    print("=" * 62)
    print("Demo 2: 编码未见词 'lowest'（语料中没有这个词）")
    print("=" * 62)
    corpus = {"low": 5, "lower": 2, "newest": 6, "widest": 3}
    merges, vocab = train_bpe(corpus, num_merges=10)
    print(f"训练出的 merge 规则（按学习顺序）: {merges}")
    for w in ["lowest", "newer", "widower"]:
        tokens = encode_word(w, merges)
        print(f"encode('{w}') -> {tokens}")
    print("-> 'lowest' 被拆成 low + est</w>：两个片段都在训练语料中高频出现，")
    print("   这就是 BPE 解决未登录词（OOV）的方式：新词由旧碎片组成")


def demo3_vocab_tradeoff():
    """扫描 merge 次数，观察 token 数与词表大小的权衡。"""
    print()
    print("=" * 62)
    print("Demo 3: 词表大小 vs 序列长度的权衡")
    print("=" * 62)
    corpus = {"low": 5, "lower": 2, "newest": 6, "widest": 3}
    text = "low lower newest widest"
    for num_merges in [0, 2, 4, 6, 8, 10]:
        merges, vocab = train_bpe(corpus, num_merges=num_merges)
        total_tokens = sum(len(encode_word(w, merges)) for w in text.split())
        print(f"merges={num_merges:>2}  vocab={len(vocab):>2}  "
              f"整句 token 数={total_tokens}")
    print("-> merges 越多：词表越大、序列越短；merges=0 退化为纯字符级")


def main():
    demo1_classic_training()
    demo2_encode_unseen()
    demo3_vocab_tradeoff()
    print()
    print("全部 demo 完成。")


if __name__ == "__main__":
    main()
