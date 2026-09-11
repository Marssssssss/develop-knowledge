# LLM（大语言模型）

## 已完成 demo

| 目录 | demo |
| --- | --- |
| [BPE分词/](./BPE分词/) | 039 BPE 分词器（训练 + rank 贪心编码 + 词表权衡） |
| [KV-Cache/](./KV-Cache/) | 040 KV Cache 与自回归解码（O(n²)→O(n) + 显存公式 + PagedAttention 分页模拟） |
| [采样策略/](./采样策略/) | 041 解码采样策略（temperature / top-k / top-p 核采样） |

> 三个 demo 构成 LLM 推理管线骨架：**文本 → token（BPE）→ 自回归解码（KV Cache）→ 下一 token 选择（采样策略）**。

## 待研究

- [ ] Transformer Decoder 架构（因果掩码 + 交叉注意力 + 生成式 vs 判别式）
- [ ] LoRA / QLoRA 微调（低秩适配 + 量化基座）
- [ ] RAG（检索增强生成：向量检索 + 上下文拼接）
- [ ] Function Calling / Tool Use（结构化输出 + 工具调度）
- [ ] 位置编码（绝对 / 相对 / RoPE 旋转位置编码）
- [ ] MoE 混合专家（稀疏激活 + 路由）
- [ ] 投机解码（小模型草稿 + 大模型验证）
- [ ] 模型量化（GPTQ / AWQ / FP8）
