#!/usr/bin/env python3
"""KV Cache 与自回归解码 + PagedAttention 分页模拟。

依据 vLLM 官方博客 (vllm.ai 2023-06-20) 与 PagedAttention 论文
(arXiv:2309.06180)：自回归解码中历史 token 的 K/V 不随新 token 变化，
缓存它们可把每步注意力从 O(n^2) 重算降为 O(n) 增量；但 KV cache 大且动态
（LLaMA-13B 单序列可达 1.7GB），传统连续预分配浪费 60%-80% 显存，
分页管理（逻辑块 -> 物理块 + 块表）把浪费压到 4% 以内。

三个 demo：
  1. 无 cache 全量重算 vs KV cache 增量：输出一致性 + 乘加次数对比
  2. KV cache 显存账单：公式计算器 + 常见模型量级核对
  3. PagedAttention 分页模拟：块表映射 + 预分配 vs 分页的浪费率对比

纯标准库，无第三方依赖。
"""

from __future__ import annotations

import math
import random

# ---------------------------------------------------------------------------
# 1) 极小自注意力（单层、d_model=8、MHA、无位置嵌入——原理演示足够）
# ---------------------------------------------------------------------------

VOCAB = 16
D = 8


class TinyAttention:
    """单层缩放点积注意力，权重用固定种子的确定性伪随机数。"""

    def __init__(self, seed: int = 42):
        rng = random.Random(seed)
        self.emb = [[rng.uniform(-0.5, 0.5) for _ in range(D)] for _ in range(VOCAB)]
        self.w_q = [[rng.uniform(-0.5, 0.5) for _ in range(D)] for _ in range(D)]
        self.w_k = [[rng.uniform(-0.5, 0.5) for _ in range(D)] for _ in range(D)]
        self.w_v = [[rng.uniform(-0.5, 0.5) for _ in range(D)] for _ in range(D)]
        self.mac = 0  # 乘加计数器（multiply-accumulate）

    def _proj(self, x, w):
        out = [0.0] * D
        for j in range(D):
            s = 0.0
            for i in range(D):
                s += x[i] * w[i][j]
                self.mac += 1
            out[j] = s
        return out

    def k_of(self, token: int):
        return self._proj(self.emb[token], self.w_k)

    def v_of(self, token: int):
        return self._proj(self.emb[token], self.w_v)

    def q_of(self, token: int):
        return self._proj(self.emb[token], self.w_q)

    def attend(self, q, ks, vs):
        """单 query 对全部 K/V 的缩放点积注意力（因果性由调用方保证）。"""
        scale = 1.0 / math.sqrt(D)
        scores = []
        for k in ks:
            s = 0.0
            for i in range(D):
                s += q[i] * k[i]
                self.mac += 1
            scores.append(s * scale)
        m = max(scores)
        exps = [math.exp(s - m) for s in scores]  # 减 max 防溢出
        total = sum(exps)
        weights = [e / total for e in exps]
        out = [0.0] * D
        for w_, v in zip(weights, vs):
            for i in range(D):
                out[i] += w_ * v[i]
                self.mac += 1
        return out


def decode_no_cache(model: TinyAttention, tokens: list[int]):
    """朴素版：每生成一步都对全部历史重新计算 K/V。"""
    outputs = []
    for t in range(1, len(tokens) + 1):
        ks = [model.k_of(tok) for tok in tokens[:t]]   # 全量重算
        vs = [model.v_of(tok) for tok in tokens[:t]]
        q = model.q_of(tokens[t - 1])
        outputs.append(model.attend(q, ks, vs))
    return outputs


def decode_with_cache(model: TinyAttention, tokens: list[int]):
    """KV cache 版：历史 K/V 只算一次并缓存，每步只算新 token 的 Q/K/V。"""
    outputs = []
    cache_k, cache_v = [], []
    for tok in tokens:
        cache_k.append(model.k_of(tok))               # 每步只算 1 个新 token
        cache_v.append(model.v_of(tok))
        q = model.q_of(tok)
        outputs.append(model.attend(q, cache_k, cache_v))
    return outputs


# ---------------------------------------------------------------------------
# 2) KV cache 显存公式
# ---------------------------------------------------------------------------

def kv_cache_bytes(seq_len: int, layers: int, kv_heads: int,
                   head_dim: int, dtype_bytes: int = 2) -> int:
    """单个序列的 KV cache 字节数。

    KV = 2(K和V各一份) * seq_len * layers * kv_heads * head_dim * dtype_bytes
    """
    return 2 * seq_len * layers * kv_heads * head_dim * dtype_bytes


# ---------------------------------------------------------------------------
# 3) PagedAttention 分页模拟（vLLM blog: 逻辑块 + 块表 + 物理块池）
# ---------------------------------------------------------------------------

class PagedKVCache:
    """块级 KV cache 管理器：块大小 block_size 个 token，按需分配物理块。"""

    def __init__(self, num_blocks: int, block_size: int = 4):
        self.block_size = block_size
        self.free_blocks = list(range(num_blocks))     # 物理块池
        self.tables: dict[int, list[int]] = {}         # seq_id -> 块表(逻辑->物理)

    def append_token(self, seq_id: int) -> bool:
        """为一个序列追加 1 个 token 的 KV；当前块满则按需分配新物理块。"""
        if seq_id not in self.tables:
            if not self.free_blocks:
                return False                           # 显存池耗尽
            self.tables[seq_id] = [self.free_blocks.pop(0)]
            return True
        used = self.total_slots(seq_id)
        if used % self.block_size == 0:                # 需要新块
            if not self.free_blocks:
                return False
            self.tables[seq_id].append(self.free_blocks.pop(0))
        return True

    def total_slots(self, seq_id: int) -> int:
        return len(self.tables[seq_id]) * self.block_size

    def physical_of(self, seq_id: int):
        """该序列实际占用的物理块编号（物理上可不连续）。"""
        return self.tables[seq_id]


def demo1_consistency_and_cost():
    print("=" * 62)
    print("Demo 1: 无 cache 全量重算 vs KV cache 增量（n=24 token）")
    print("=" * 62)
    rng = random.Random(7)
    tokens = [rng.randrange(VOCAB) for _ in range(24)]

    m1 = TinyAttention(seed=1)
    out_nocache = decode_no_cache(m1, tokens)
    m2 = TinyAttention(seed=1)
    out_cache = decode_with_cache(m2, tokens)

    n = len(tokens)
    consistent = all(
        abs(a - b) < 1e-9
        for oa, ob in zip(out_nocache, out_cache)
        for a, b in zip(oa, ob)
    )
    print(f"两版输出逐元素一致（容差 1e-9）: {consistent}")
    print(f"无 cache 乘加次数: {m1.mac:>10,}  ~ O(n^2 * d)")
    print(f"KV cache 乘加次数: {m2.mac:>10,}  ~ O(n * d)")
    print(f"计算量节省比例: {1 - m2.mac / m1.mac:.1%}")
    print("-> 历史 token 的 K/V 与新 token 无关，重算是纯浪费；")
    print("   cache 用显存换算力：注意 O(n) 的 QK 点积仍在，省的是 K/V 投影")


def demo2_memory_bill():
    print()
    print("=" * 62)
    print("Demo 2: KV cache 显存账单（FP16 = 2 字节/元素）")
    print("=" * 62)
    models = [
        # (名称, 层数, kv_heads, head_dim)
        ("LLaMA-13B (MHA)", 40, 40, 128),
        ("Llama-3-8B (GQA)", 32, 8, 128),
        ("7B (GQA) @ 32K 上下文", 32, 8, 128),
    ]
    cases = [(2048, models[0]), (2048, models[1]), (32768, models[2])]
    for seq_len, (name, layers, kv_heads, head_dim) in cases:
        b = kv_cache_bytes(seq_len, layers, kv_heads, head_dim, dtype_bytes=2)
        print(f"{name:<24} seq_len={seq_len:<6} -> {b / 1e9:.2f} GB")
    print("-> LLaMA-13B 单序列 2048 token 约 1.68 GB，与 vLLM 博客")
    print("   '1.7GB for a single sequence in LLaMA-13B' 一致；")
    print("   GQA 减少 kv_heads 数量，是比 MHA 显存占用小数倍的根源")


def demo3_paged_attention():
    print()
    print("=" * 62)
    print("Demo 3: PagedAttention 分页 vs 连续预分配（block_size=4）")
    print("=" * 62)
    # 三个请求：实际 KV 长度 7/12/5，系统最大长度按 16 预留
    seqs = {0: 7, 1: 12, 2: 5}
    max_len = 16

    total_actual = sum(seqs.values())
    total_reserved = max_len * len(seqs)
    print(f"[连续预分配] 实际 {total_actual} 槽 / 预留 {total_reserved} 槽"
          f" -> 浪费 {1 - total_actual / total_reserved:.0%}")
    print("   （vLLM 博客：现有系统浪费 60%-80%，含保留/内部/外部碎片）")

    paged = PagedKVCache(num_blocks=16, block_size=4)
    for seq_id, length in seqs.items():
        for _ in range(length):
            assert paged.append_token(seq_id)
        print(f"[分页] 请求 {seq_id}: 实际 {length} token, 块表 = "
              f"{paged.physical_of(seq_id)}（物理块可不连续）")
    used_slots = sum(paged.total_slots(s) for s in seqs)
    waste = used_slots - total_actual
    print(f"[分页] 实际 {total_actual} 槽 / 分配 {used_slots} 槽"
          f" -> 浪费 {waste} 槽（仅各序列最后一个块的尾部）")
    print("   vLLM: 'memory waste only happens in the last block'，< 4%")
    print("-> 类比 OS 虚拟内存：块 = 页，token = 字节，序列 = 进程，")
    print("   块表 = 页表；按需分配 + 逻辑连续/物理不连续")


def main():
    demo1_consistency_and_cost()
    demo2_memory_bill()
    demo3_paged_attention()
    print()
    print("全部 demo 完成。")


if __name__ == "__main__":
    main()
