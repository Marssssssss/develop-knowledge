# FlashAttention 的在线 softmax

`Dao-AILab/flash-attention` → `flash_attn/flash_attn_triton.py` 中 `_fwd_kernel` 的逐行转写。它把注意力的 `QK^T` **永不落地**，靠一个递推公式边扫边合并。

## 一、递推式（源码写法）

```
初始化：lse_i = −inf,  m_i = −inf,  acc_o = 0

for 每个 K/V 块:
    qk     = q · k^T · softmax_scale            # 有 bias 时是 qk·scale + bias
    m_ij   = max(max(qk, 1) · softmax_scale, lse_i)
    p      = exp(qk · softmax_scale − m_ij)
    l_ij   = Σ p
    acc_o *= exp(m_i − m_ij)                    # m_i 是**上一轮**的 max
    acc_o += p @ v
    m_i    = m_ij
    l_i_new = exp(lse_i − m_ij) + l_ij
    lse_i  = m_ij + log(l_i_new)

收尾： acc_o *= exp(m_i − lse_i)
```

## 二、两个容易被写错的地方

### 2.1 维护的是 `(m_i, lse_i)`，不是 `(m_i, l_i)`

教科书版在线 softmax 通常记 `(running_max, running_sum)`，最后除以 `l_i`。源码记的是 `(m_i, lse_i)`——**running log-sum-exp**。两者关系：

```
lse_i = m_i + log(l_i)          ⇒  1/l_i = exp(m_i − lse_i)
```

于是收尾的 `acc_o *= exp(m_i − lse_i)` 就是「除以归一化分母」，而且天然避开了 `l_i → 0` 的除零（`lse_i = −inf` 时 `exp(m_i − lse_i)` 也是 0）。返回的 `lse` 还有第二个用途：反向传播要用它重算 softmax。

本 demo 直接断言 `exp(lse_i) == Σ_j exp(score_ij)`，以及 `lse_i ≥ 该行最大 score`。

### 2.2 `m_ij` 的比较基准是 `lse_i` 而不是 `m_i`

```
m_ij = tl.maximum(tl.max(qk, 1) * softmax_scale, lse_i)
```

因为 `lse = m + log(l) ≥ m`，拿 `lse_i` 当 running max 的基准只会把 `m_ij` 抬得更高，而 `p = exp(qk − m_ij)` 与 `acc *= exp(m_old − m_ij)` 两个缩放**互相抵消**，结果不变。本 demo 用 `use_lse_for_max` 开关把两种基准各跑一遍，断言**逐位相同**（非因果与因果各一组）。

## 三、为什么它和普通 softmax 完全等价

朴素注意力是「整张 `S×S` 落地 → 减全局 max → exp → 求和 → 除」；分块版每一块都只减**当前块**的 max，然后在下一块用 `exp(m_old − m_new)` 把已有的累加器**重新缩放**回新基准。

本 demo 用多种分块组合与朴素实现对比（`S_q=24, S_k=20, d=8`）：

| 分块 | 最大偏差 |
| --- | --- |
| 128 × 128（源码默认值） | < 1e-15 |
| 8 × 8 | < 1e-15 |
| 3 × 7（非整除、非对称） | < 1e-15 |
| 24 × 1（`BLOCK_N=1`，逐列扫） | < 1e-15 |

**分块大小不影响结果**——这正是「在线」的含义：FlashAttention 省的是显存带宽，不是算术。

## 四、因果掩码：循环上界而不是逐元素掩码

```
end_n = seqlen_k if not IS_CAUSAL else tl.minimum((start_m + 1) * BLOCK_M, seqlen_k)
qk += tl.where(offs_m[:, None] >= (start_n + offs_n)[None, :], 0, float("-inf"))
```

两道保险：① 循环只跑到对角线所在块为止（上三角的整块直接不访存）；② 块内仍逐元素写 `−inf`，因为块边界不与对角线对齐。

支持集探针（本 demo 的核心断言之一）：把 `k[6..9]` 整体加 5，因果模式下**第 0~5 行逐位不变**、第 6~9 行变化；关掉 causal 后同样的改动会波及所有行（负控）。

> 源码启动处 `BLOCK_M` 与 `BLOCK_N` 取同一个 `BLOCK`（128），所以第一个块必然包含对角线，不会出现「整块都被掩码、且还没有历史」的退化情形（那种情形下 `−inf − (−inf)` 是 NaN）。本实现对该退化显式置 0，正常参数下不会触发。

## 五、数值稳定性

`q = [50,50,50,50]`、`k = [50,50,50,50]`（`d=4`）时 `q·k = 10000`，`scale = 1/2` → logit **5000**：

```
朴素 exp(5000)          → OverflowError（float64 上限约 709）
在线 softmax 的 lse     → 5000.000000（有限）
输出                    → [1, 0, 0, 0]（注意力全部落在 v[0]）
```

关键在于**每一块都减掉该块的 max**，`exp` 的自变量恒 ≤ 0。

## 六、其它源码细节

| 细节 | 取值 / 行为 |
| --- | --- |
| `softmax_scale` | 缺省 `1/sqrt(d)`；bias 存在时必须在 `exp` **之前**乘到 `qk` 上（源码注释：方便编译器把乘加融合成 FMA） |
| `seqlen_q_rounded` | `ceil(seqlen_q/128)*128`，是 `lse` / `tmp` 缓冲的对齐长度，不是序列长度 |
| `d` 的限制 | 源码断言 `d <= 128`（`assert d <= 128, "FlashAttention only support head dimensions up to 128"`） |
| dtype 限制 | 只支持 fp16 / bf16 且必须在 CUDA 上（本 demo 用 float64 复现算法，不模拟 dtype 限制） |
| bias 形状 | `(1, seqlen_k)` 叫 vector、`(seqlen_q, seqlen_k)` 叫 matrix |
| 常数 bias | 不改变输出（softmax 平移不变），本 demo 单独断言 |

## 七、代码结构

| 文件 | 说明 |
| --- | --- |
| `python/flash.py` | `_fwd_kernel` 的在线 softmax + 朴素对照 + `naive_lse` |
| `python/selfcheck_flash.py` | 48 条断言（实跑全绿） |
| `python/main.py` | 演示入口 |
| `go/flash.go` + `go/main.go` | Go 同题实现（无本机工具链，走机械核查） |

运行：

```bash
cd python && python selfcheck_flash.py && python main.py
```

## 八、参考资料（实际读过）

- `Dao-AILab/flash-attention@main` — `flash_attn/flash_attn_triton.py`（41.1 KB）：`_fwd_kernel` 的在线 softmax 递推（141–284 行）、`_flash_attn_forward` 的形状断言与 `softmax_scale` 缺省值、`seqlen_q_rounded` 与 `BLOCK_M/BLOCK_N` 启动参数（812–891 行）
- Dao et al., *FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness*, arXiv:2205.14135（本轮 arXiv PDF 抓取被连接重置，未取到全文；算法细节以仓库源码为准，论文仅作为出处标注）
