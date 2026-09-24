# 卷积变体：分组 / 空洞 / 转置

`pytorch/pytorch` → `torch/nn/modules/conv.py`（79.7 KB）的逐行转写。三大变体在 PyTorch 里不是三个类，而是**同一组公式上的三个开关**（`groups` / `dilation` / `transposed` + `output_padding`）。

## 一、Conv2d 的尺寸公式与等效核

```
H_out = ⌊(H_in + 2·padding[0] − dilation[0]·(kernel_size[0] − 1) − 1) / stride[0] + 1⌋
```

空洞卷积的等效核是 `k_eff = dilation·(k−1) + 1`，于是「空洞 + 无 padding」等价于「`k_eff` 的普通核 + 无 padding」：

| H_in=32, k=3 | k_eff | H_out |
| --- | --- | --- |
| pad=0, dil=1, stride=1 | 3 | 30 |
| pad=1, dil=1, stride=1 | 3 | 32 |
| pad=0, dil=2, stride=1 | 5 | 28 |
| pad=1, dil=2, stride=2 | 5 | 15 |

`dilation=1` 时 `k_eff == k`，公式退化成最常见的形式。取整是**向下取整**：`H=8, k=3, stride=2` 给 `⌊5/2⌋+1 = 3` 而不是 4。尺寸算出来 ≤ 0 时 PyTorch 抛 `Calculated output size too small`（本 demo 复现了 `H=2, k=5 → −2` 的中间值）。

`padding='same'` 在源码 Note 里明确写了**只支持 stride=1**（本 demo 对 `stride=2` 抛 `ValueError`）。

## 二、groups：一张连接图

`weight` 的形状是 `(out_channels, in_channels/groups, kH, kW)`，`in_channels` 与 `out_channels` **都必须**能被 `groups` 整除。第 `g` 组只用第 `g` 段输入通道、产出第 `g` 段输出通道。

本 demo 用**支持集探针**验证：把某一个输入通道单独置 1、其余置 0，看哪些输出通道被点亮。

| 配置 | 探针结果 |
| --- | --- |
| `groups=2`（4 进 4 出） | `in0/in1 → out0/out1`，`in2/in3 → out2/out3` |
| `groups=4 = in_channels`（depthwise） | `in_j → out_j`，一一对应 |
| `groups=4`，`out=8`（multiplier K=2） | `in_j → out[2j], out[2j+1]` |
| `groups=1`（负控） | 任一输入通道点亮**全部**输出通道 |

源码注释把 `groups == in_channels` 且 `out_channels == K·in_channels` 的情形叫做 **depthwise convolution**；`groups=1` 时两个「各看一半通道、各产出一半、再拼接」的卷积等价于标准卷积（源码 `groups_note` 原话）。

参数量（`C_in=3, C_out=16, k=3`，含 bias）：

```
标准 Conv2d      : 16·3·3·3 + 16 = 448
Depthwise (3→3)  : 3·1·3·3 + 3  = 30
Pointwise 1×1    : 16·3·1·1 + 16 = 64
DW + PW          : 94            ≈ 标准的 21.0%
```

## 三、转置卷积：`output_padding` 只能补 `[0, stride−1]`

转置卷积的输出尺寸：

```
H_out = (H_in − 1)·stride − 2·padding + dilation·(k − 1) + output_padding + 1
```

关键在 `_output_padding`：给定 `output_size`，先算出

```
min = (H_in − 1)·stride − 2·padding + dilation·(k−1) + 1
max = min + stride − 1
output_padding = output_size − min
```

`output_size` 落在 `[min, max]` 之外就抛

```
ValueError: requested an output size of N, but valid sizes range from min to max (for an input of ...)
```

也就是说：**`stride` 决定了转置卷积输出尺寸的歧义档数**。`H_in=4, k=3, stride=2, pad=1` 时 `min=7, max=8`，只能选 7 或 8；`stride=1` 时区间退化成单点，`output_padding` 完全不起作用。

> 这条解释了为什么上采样网络里 `stride=2` 的转置卷积常常伴随 `output_padding=1`：不加它尺寸会少一格。

## 四、padding_mode

源码 `padding_mode` 的取值是 `'zeros'` / `'reflect'` / `'replicate'` / `'circular'`，默认 `'zeros'`。**这些模式的实现在 C++ 侧，本文件只给取值列表**，因此本 demo 只按命名建模其中三种（`zeros` 补 0、`replicate` 取边界值、`circular` 按下标取模），并在遇到未知模式时报错；`reflect` 未建模（与 `replicate` 只在内缩一格时有差异，不做无据推断）。

同一个 `1×3×3` 输入配 `2×2` 核 `[[1,2],[3,4]]`、`padding=1`，左上角输出：

| mode | 计算 | 结果 |
| --- | --- | --- |
| zeros | `4·x[0][0]` | 4 |
| replicate | `1+2+3+4` 全部乘 `x[0][0]=1` | 10 |
| circular | `1·x[2][2] + 2·x[2][0] + 3·x[0][2] + 4·x[0][0] = 9+14+9+4` | 36 |

## 五、代码结构

| 文件 | 说明 |
| --- | --- |
| `python/conv.py` | 尺寸公式 + `weight_shape` / 参数量 + 支持 groups/dilation/padding_mode 的前向 + 支持集探针 |
| `python/selfcheck_conv.py` | 54 条断言（实跑全绿） |
| `python/main.py` | 演示入口 |
| `go/conv.go` + `go/main.go` | Go 同题实现（无本机工具链，走机械核查） |

运行：

```bash
cd python && python selfcheck_conv.py && python main.py
```

## 六、参考资料（实际读过）

- `pytorch/pytorch@main` — `torch/nn/modules/conv.py`：`_ConvNd` 的常量表与 `output_padding` 字段（55–134 行）、`convolution_notes` 里的 `groups_note` 与 `depthwise_separable_note`（40–52 行）、`Conv2d` 的 Shape 公式与 `padding='same'` 限制（388–465 行）、`_output_padding` 的 `min_sizes`/`max_sizes` 与越界报错（778–829 行）
- `pytorch/pytorch@main` — `torch/nn/modules/conv.py` 的 `ConvTranspose2d/3d` 构造与 `forward` 的 `output_size` 通路
