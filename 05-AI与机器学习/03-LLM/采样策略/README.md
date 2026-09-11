# 解码采样策略（temperature / top-k / top-p）

## 简介

- LLM 生成文本是自回归过程：`P(w_1:T | W_0) = ∏ P(w_t | w_1:t-1, W_0)`，每步从模型输出的 logits 分布中确定下一个 token。**解码策略**决定"如何从分布里挑"。
- 贪心（每步取 argmax）确定但极易陷入重复循环；采样类策略引入受控随机性，是开放域生成的主流做法。
- 关键概念：
  - **greedy**：每步取概率最高 token；`temperature=0` 与之等价（HF 官方说明）
  - **temperature**：`p = softmax(z/T)`；T<1 分布更尖更确定，T>1 更平更多样
  - **top-k**：只在概率最高的 k 个候选中采样（HF 文档：常用 5–50）
  - **top-p / nucleus sampling**：按概率降序累积，保留覆盖 p 概率质量的最小候选集（"核"），候选数随分布形状**动态**变化

历史背景：nucleus sampling 由 Holtzman et al. 2019 提出，动机是人类语言每步的实际候选词数量差异极大——固定 k 的 top-k 在尖峰分布时保留过多噪声、平坦分布时又截断过早。

## 原理详解

四类策略的决策流程：

```
logits (vocab 维)
   │
   ├─ greedy ──────────────> argmax（确定性；易重复）
   │
   └─ 采样类：
        ① temperature:  z_i' = z_i / T ──> softmax ──> 分布形态调整
        ② top-k:        保留 k 个最高概率候选，其余置 0，重归一化
        ② top-p:        降序累积到概率质量 p 为止，其余置 0，重归一化
        ③ draw:         从截断后的分布中随机抽一个
```

temperature 的数学效果（demo 1）：softmax 的指数放大/缩小 logits 差异。T→0 时最大 logit 的概率 →1，分布退化为 one-hot，与 greedy 完全一致；熵随 T 单调上升。

top-k 与 top-p 的本质区别（demo 2 / 3）：

| | 候选数 | 尖峰分布 | 平坦分布 |
| --- | --- | --- | --- |
| top-k | 固定 k 个 | 仍带着低概率长尾 | 可能砍掉合理候选 |
| top-p | 动态（覆盖质量 p） | 可能只留 1–2 个 | 保留几乎所有候选 |

top-p 实现细节：降序累积，**一旦 cum ≥ p 即停止**；HF 实现约定 `min_tokens_to_keep=1`（极端 p 也至少保留最高概率 token，避免空集）。截断后必须**重归一化**再采样。

## 对比 / 选型

| 策略 | 确定性 | 多样性 | 典型场景 |
| --- | --- | --- | --- |
| greedy | 完全确定 | 无 | 代码、抽取、分类式任务 |
| beam search | 确定（保留 num_beams 候选） | 低 | 翻译、摘要等有参考答案的任务 |
| temperature + top-p | 随机 | 可调 | 开放域对话、创作（常用 T≈0.7-0.9, p≈0.9） |
| top-k | 随机 | 固定候选数 | 传统方案，多被 top-p 取代 |

## 环境准备

- 操作系统：任意（纯标准库）
- 语言版本：Python 3.8+
- 依赖：无

## 运行方式

```bash
python3 sampling.py
```

## 关键代码片段

top-p 截断（对应原理详解"动态候选集"）：

```python
def top_p_filter(probs, p):
    order = sorted(range(len(probs)), key=lambda i: -probs[i])
    filtered = [0.0] * len(probs)
    cum = 0.0
    for idx in order:                  # 按概率降序
        filtered[idx] = probs[idx]
        cum += probs[idx]
        if cum >= p:                   # 覆盖 p 质量即停止
            break                      # （隐含 min_tokens_to_keep=1）
    total = sum(filtered)
    return [x / total for x in filtered]  # 重归一化后才能采样
```

softmax 数值稳定（减 max 再取指数，防大 logits 溢出）：

```python
def softmax(logits):
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    return [e / sum(exps) for e in exps]
```

## 性能与边界

- 每步解码的采样开销：排序 O(V log V)（V=词表大小，约 10⁵），相对前向计算可忽略
- demo 4 用 20000 次采样的频率近似理论概率，验证 temperature→top-p→draw 管线的正确性（大数定律）
- 生产上各参数来自模型 `generation_config.json`（HF：temperature 默认 1.0、top_k 默认 50）

## 注意事项与常见坑

- **temperature=0 等价 greedy**：HF 推理文档明确；设置 temperature/top_k/top_p 会自动启用 do_sample。
- **先 temperature 后截断**：顺序是 logits 缩放 → top-k/top-p 过滤 → 重归一化 → 采样；先截断后缩放结果不同。
- **top-p 重归一化不可省**：截断后概率和 < 1，直接采样会导致整体采样率缺失、低概率 token 相对权重错误。
- **并列概率的排序稳定性**：相同概率 token 的保留顺序取决于排序稳定性，工程实现需确定性 tie-break（本实现按原始下标稳定排序）。
- **贪心的重复陷阱**：HF 博客中 GPT-2 贪心输出很快开始复读（"I'm not sure if I'll ever be able to..."循环）；这是采样策略存在的核心理由。
- **renormalize_logits**：HF 新版 GenerationConfig 建议开启，因为某些 logits 处理器（如 repetition penalty）会破坏归一化。

## 参考资料（实际阅读过的权威来源）

- [如何生成文本：通过 Transformers 用不同的解码方法生成文本 (Patrick von Platen, HuggingFace 官方博客中文版)](https://huggingface.co/blog/zh/how-to-generate) — 贪心/波束/top-k/top-p 完整定义、GPT-2 实例、贪心重复现象、自回归概率分解公式
- [Inference for PROs (HuggingFace 官方博客)](https://huggingface.co/blog/inference-pro) — temperature=0 等价 greedy、top_k 典型值 10–50、top_p=0.9 的概率质量含义
- [Transformers GenerationConfig 文档](https://huggingface.co/docs/transformers/main_classes/text_generation) — temperature/top_k/top_p 默认值、TopK/TopPLogitsWarper 行为约定（min_tokens_to_keep）
