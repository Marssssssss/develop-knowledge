# KV Cache 与自回归解码（含 PagedAttention 分页模拟）

## 简介

- 自回归解码中，每生成一个新 token 只需其 Query；历史 token 的 Key/Value 不随新 token 变化——把它们缓存起来（KV cache）可把每步注意力从全量重算 O(n²) 降为增量 O(n)，是 LLM 推理必备优化。
- 代价是显存：KV cache 大且动态（vLLM 博客：LLaMA-13B 单序列可达 1.7 GB），传统"按最大长度连续预分配"浪费 60%–80% 显存。
- 关键概念：
  - **Prefill 阶段**：一次性并行处理全部 prompt token，构建初始 KV cache，计算密集（决定首 token 时延 TTFT）
  - **Decode 阶段**：逐 token 生成，每步只算新 token 的 Q/K/V，内存带宽受限（决定 token 间时延 TPOT）
  - **显存公式**：`2 × seq_len × layers × n_kv_heads × head_dim × dtype_bytes`
  - **PagedAttention**：借鉴 OS 虚拟内存分页——块=页、token=字节、序列=进程、块表=页表，逻辑连续/物理不连续，按需分配

历史背景：PagedAttention 由 UC Berkeley 等提出（SOSP 2023 论文），是 vLLM 推理引擎的核心；相对 HuggingFace Transformers 实现最高 24× 吞吐。

## 原理详解

无 cache vs 有 cache 的每步计算：

```
无 cache（第 t 步）: 对前 t 个 token 全部重新投影 K/V   -> O(t·d) 次投影
                    第 t 步累计                       -> O(n²·d) 总投影量

KV cache（第 t 步）: 只投影第 t 个 token 的 K/V (缓存追加) -> O(d) 次投影
                    query 仍与全部历史 K 点积            -> O(n·d) 总量
```

两版输出**逐元素一致**（demo 1 验证）：缓存不改变数学，只是不重复计算不变量。

PagedAttention 内存管理（vLLM）：

```
逻辑视角（序列内连续）:  [tok 0-3][tok 4-7][tok 8-11][tok 12-...]
                            |         |         |
块表 (block table):      逻辑块0   逻辑块1   逻辑块2        （= OS 页表）
                            |         |         |
物理视角（可不连续）:    物理块7   物理块3   物理块12       （= 物理页帧）
```

- 物理块**按需分配**：新 token 写满当前块才申请下一块
- 浪费只发生在序列最后一个块的尾部 —— 实测 < 4%（对比预分配的 60%-80%）
- **共享与 Copy-on-Write**：并行采样（同一 prompt 多路输出）共享 prompt 的物理块，引用计数管理，分叉写入时才复制；复杂采样内存省 55%，吞吐 +2.2×

## 对比 / 选型

| 策略 | 浪费率 | 共享前缀 | 适用 |
| --- | --- | --- | --- |
| 连续预分配（HF 原生） | 60%–80% | 不支持 | 原型/单请求 |
| PagedAttention（vLLM） | < 4% | CoW 共享 | 高并发服务 |
| RadixAttention（SGLang） | 低 | 前缀树任意长度复用 | 多轮对话/RAG |

## 环境准备

- 操作系统：任意（纯标准库，无 GPU 依赖）
- 语言版本：Python 3.8+
- 依赖：无

## 运行方式

```bash
python3 kv_cache.py
```

## 关键代码片段

增量解码（对应"KV cache"）：

```python
def decode_with_cache(model, tokens):
    outputs, cache_k, cache_v = [], [], []
    for tok in tokens:
        cache_k.append(model.k_of(tok))   # 每步只投影 1 个新 token
        cache_v.append(model.v_of(tok))
        q = model.q_of(tok)
        outputs.append(model.attend(q, cache_k, cache_v))
    return outputs
```

显存公式（对应 demo 2）：

```python
def kv_cache_bytes(seq_len, layers, kv_heads, head_dim, dtype_bytes=2):
    # 2 = K 与 V 各一份；FP16 每元素 2 字节
    return 2 * seq_len * layers * kv_heads * head_dim * dtype_bytes
```

## 性能与边界

- 计算量：无 cache 总投影 O(n²·d) → cache 后 O(n·d)；demo 1 实测 n=24 时节省约 50% 乘加（QK 点积部分两者相同，省的是 K/V 重复投影）
- 显存量级（FP16）：LLaMA-13B(MHA, 40层×40头×128) 单序列 2048 token ≈ 1.68 GB（与 vLLM 博客 "1.7GB" 一致）；Llama-3-8B(GQA, 32层×8 KV头×128) 同长度仅 ≈ 0.27 GB——GQA 靠减少 KV 头数把 cache 压小一个量级
- 7B GQA 模型 32K 上下文 ≈ 4.3 GB；上下文翻倍 cache 线性增长

## 注意事项与常见坑

- **KV cache 省的是 K/V 投影，不省 QK 点积**：第 t 步的 Q 仍要与全部历史 K 点积（O(n)），这是 decode 阶段内存带宽受限的根源。
- **同一 token 在不同位置 KV 不同**：K/V 由"该 token 的嵌入"投影而来，注意力语义上每个位置独立缓存；prefix 共享要求前缀完全一致。
- **块大小权衡**：vLLM 默认 16 token/块（GPU warp 32 线程对齐、合并内存访问）；块越大碎片越多、间接寻址越少，TensorRT-LLM 用 64–128。
- **预分配的三种浪费**：保留（预留未用）、内部碎片（超实际长度）、外部碎片（连续块之间的空洞）——分页只消除后两者及大部分保留浪费。
- **GQA/MQA 是显存的第一杠杆**：推理成本里 KV cache 常占总显存 50%–80%，减少 KV 头数比任何管理策略都直接。

## 参考资料（实际阅读过的权威来源）

- [vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention (vLLM 官方博客, 2023-06-20)](https://blog.vllm.ai/2023/06/20/vllm.html) — KV cache 1.7GB/LLaMA-13B、60%-80% 浪费、逻辑/物理块与块表、CoW 共享、<4% 浪费与吞吐数据（全文实际阅读）
- [Efficient Memory Management for Large Language Model Serving with PagedAttention (Kwon et al., SOSP 2023, arXiv:2309.06180)](https://arxiv.org/abs/2309.06180) — PagedAttention 论文：预填充/解码两阶段、三类内存浪费的形式化定义
- [HuggingFace Transformers GenerationConfig 文档](https://huggingface.co/docs/transformers/main_classes/text_generation) — `use_cache` 参数与 DynamicCache/StaticCache 等缓存实现类型
