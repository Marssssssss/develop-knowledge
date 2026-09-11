# BPE 分词器（Byte Pair Encoding）

## 简介

- BPE 是把文本切成子词（subword）单元的分词算法：从字符（或字节）出发，迭代合并语料中最高频的相邻符号对，直到词表达到目标大小。GPT-2 / GPT-4 / RoBERTa 等主流 LLM 的 tokenizer 均为（byte-level）BPE。
- 解决的问题：词级词表爆炸 + 未登录词（OOV）不可表示；字符级序列过长。子词级两头兼顾——高频词保为单 token，罕见词拆成可复用碎片。
- 关键概念：
  - **merge 规则表**：训练产物，有序列表，每条规则 `(left, right)` 表示把相邻的 `left right` 合并为 `left+right`
  - **rank**：规则的学习顺序编号；编码新文本时按 rank 贪心应用，保证切分唯一确定
  - **词尾标记 `</w>`**：区分"词内片段"与"完整词"，使 `est</w>`（词尾）与 `est`（词中）成为不同 token
  - **byte-level BPE**：GPT-2 起以 256 个原始字节为基础字母表，任何 Unicode 字符都能编码，彻底消除 OOV

历史背景：BPE 原是 Philip Gage 1994 年提出的数据压缩算法；Sennrich、Haddow、Birch 在 ACL 2016 论文中将其改造为 NMT 的子词分段方法，使固定词表模型具备开放词表翻译能力（WMT15 英德/英俄 +1.1/+1.3 BLEU）。

## 原理详解

训练（在词频表上循环）：

```
1. Seed    : 每个词拆成单字符序列，末尾加 </w>；基础词表 = 全部字符
2. Count   : 跨语料统计所有相邻符号对的出现次数（按词频加权）
3. Merge   : 取最高频对 (A,B)，产生新符号 AB 加入词表，
             把所有词中的 "A B" 替换为 "AB"，记录规则 (A,B)
4. Repeat  : 直到达到目标 merge 次数 / 不再有出现 >=2 次的对
```

经典示例（Sennrich 2016 语料：low×5, lower×2, newest×6, widest×3）：

```
第 1 轮: (e,s)   出现 9 次 (newest×6 + widest×3) -> "es"
第 2 轮: (es,t)  出现 9 次                        -> "est"
第 3 轮: (est,</w>) 9 次                          -> "est</w>"
第 4 轮: (l,o)   出现 7 次 (low×5 + lower×2)      -> "lo"
第 5 轮: (lo,w)  出现 7 次                        -> "low"
...
```

编码新词（`encode`）：**不按新文本里的频率合并**，而是每步在当前符号序列中找 rank 最小（最早学到）的可应用规则并应用，循环到无规则可用。这是保证"同一份规则表对任何输入产生唯一确定切分"的关键——顺序即语义。

复杂度：训练朴素实现每轮重扫语料为 O(N·V)；生产实现（HF tokenizers、SentencePiece）用优先队列增量更新到约 O(N log V)。编码单词 O(M²) 最坏（M 为符号数），M 很小故近似线性。

## 对比 / 选型

| 算法 | 合并依据 | 代表模型 | 特点 |
| --- | --- | --- | --- |
| BPE | 相邻对频率最高 | GPT-2/4、RoBERTa、Llama | 简单高效，最主流 |
| WordPiece | 似然增益 `P(xy)/(P(x)P(y))` 最大 | BERT、Electra | 概率视角选合并 |
| SentencePiece | 原始字符流，空格即字符 | T5、ALBERT、多数中日韩模型 | 语言无关，适合无空格语言 |
| Unigram LM | 删token损失最小 | mT5、部分日文模型 | 概率式多候选切分 |

## 环境准备

- 操作系统：任意（纯标准库）
- 语言版本：Python 3.8+
- 依赖：无

## 运行方式

```bash
python3 bpe.py
```

## 关键代码片段

编码按 rank 贪心应用规则（对应原理详解"编码新词"）：

```python
def encode_word(word, merges):
    rank = {pair: i for i, pair in enumerate(merges)}
    syms = list(word) + [WORD_END]
    while True:
        best_i, best_rank = -1, None
        for i in range(len(syms) - 1):          # 扫描相邻对
            r = rank.get((syms[i], syms[i + 1]))
            if r is not None and (best_rank is None or r < best_rank):
                best_i, best_rank = i, r        # 记住 rank 最小的
        if best_i < 0:
            return syms                          # 无规则可用，终止
        left, right = syms[best_i], syms[best_i + 1]
        syms[best_i:best_i + 2] = [left + right] # 应用一次合并
```

合并替换需从左到右线性扫描（`_apply_merge_all`），避免 `A A A` 型重叠对被错误地合并两次。

## 性能与边界

- 训练：O(N·V) 朴素 / O(N log V) 优先队列优化（N=语料符号数，V=merge 数）
- 编码：单词 O(M²) 最坏；GPT-2 实际词表约 50k，GPT-4 时代 100k+
- 本 demo 为字符级（经典 Sennrich 风格）；byte-level 需以 256 字节为基础词表，思路完全相同

## 注意事项与常见坑

- **编码顺序 ≠ 频率顺序**：对已训练 tokenizer，编码时按规则学习顺序（rank）应用，而不是按新文本中的频率——否则同一 tokenizer 对同一文本产生不稳定切分。
- **并列最高频对**：需要确定性 tie-break（本实现按对字典序），否则训练结果不可复现。
- **词尾标记不可省**：没有 `</w>` 时 `est`（词中）与 `est`（词尾）不可区分，模型无法学到"词边界"信息。
- **byte-level 的意义**：字符级遇到罕见 Unicode / emoji 仍会 OOV；GPT-2 用 256 字节作基础字母表后理论 OOV 为零。
- **数字切分问题**：BPE 常把 `2024` 切成任意碎片（如 `202`+`4`），这是 LLM 算术能力弱的原因之一。

## 参考资料（实际阅读过的权威来源）

- [Neural Machine Translation of Rare Words with Subword Units (Sennrich, Haddow, Birch, ACL 2016)](https://arxiv.org/abs/1508.07909) — BPE 引入 NMT 的原始论文：开放词表翻译、子词单元动机与 BLEU 收益
- [Byte Pair Encoding (unseel.com)](https://unseel.com/cs/byte-pair-encoding) — 算法分步、rank 贪心编码规则、训练/编码复杂度、byte-level BPE 与 GPT 系列关系
- [Lecture 6: Tokenization (context-lab LLM course)](https://context-lab.com/llm-course/slides/week2/lecture6.pdf) — WordPiece 似然打分公式、SentencePiece 动机、GPT-2 BPE 实例
