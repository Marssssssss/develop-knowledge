# 量化与校准

`pytorch/pytorch` → `torch/ao/quantization/observer.py`、`fake_quantize.py`、`utils.py` 的逐行转写。核心问题是：**用 8 bit 表示浮点张量时，scale 和 zero_point 怎么算、观测范围怎么定**。

## 一、qmin/qmax 的档位表

`utils.calculate_qmin_qmax` 决定量化网格的两端：

| dtype | 默认 | `reduce_range=True` |
| --- | --- | --- |
| `qint8` / `int8` | (−128, 127) | (−64, 63) |
| `quint8` / `uint8` | (0, 255) | (0, 127) |
| `qint32` / `int32` | (−2³¹, 2³¹−1) | 同左（不折半） |
| `uint16` | (0, 65535) | 同左 |
| `int16` | (−2¹⁵, 2¹⁵−1) | 同左 |
| 其它 | (0, 15) | 同左 |

`reduce_range` 只在 8 bit 上生效（源码注释：为避免指令溢出），且**已标记 deprecated**（构造时会 warn「请用 quant_min/quant_max」）。自定义 qrange 时它是 `//2` 整除折半，而不是乘 0.5——`(0,100) → (0,50)`。

## 二、`_calculate_qparams` 的三条分支

```
min_val_neg = min(min_val, 0)        # 关键：0 恒被纳入量程
max_val_pos = max(max_val, 0)
```

### 2.1 symmetric

```
max_val_pos = max(−min_val_neg, max_val_pos)
scale       = max_val_pos / ((qmax − qmin) / 2)
scale       = max(scale, eps)
zero_point  = 128                    # quint8；uint16 是 2^15
```

### 2.2 affine（默认）

```
scale      = (max_val_pos − min_val_neg) / (qmax − qmin)
scale      = max(scale, eps)
zero_point = qmin − round(min_val_neg / scale)      # 再 clamp 到 [qmin, qmax]
```

### 2.3 per_channel_affine_float_qparams

```
scale      = (max_val − min_val) / (qmax − qmin)     # 不做 0 截断
scale      = scale > eps ? scale : 1.0
zero_point = −min_val / scale                        # 浮点，不取整
```

### 对称 vs 仿射的代价（本 demo 最有意思的一组对照）

数据是 **ReLU 之后的 `[0, 1]`**：

| qscheme | scale | zero_point |
| --- | --- | --- |
| affine | `1/255` | 0 |
| symmetric | `2/255` | 128 |

`symmetric` 因为 `max(−min_neg, max_pos) = 1` 把量程撑成 `[−1, 1]`，**一半的量化档位落在永远用不到的负半边**，分辨率整整差一倍。这就是为什么激活（ReLU 后非负）通常用 affine、权重（零均值分布）才用 symmetric。

另外两个容易踩的点：

- `min == max = 0.3` 时 **scale 不是 0**，而是 `0.3/255`（`min_val_neg` 被压到 0，量程是 `[0, 0.3]`）。只有全零张量才会触发 `eps` 兜底。
- `round` 是**半数取偶**（`torch.round` 语义）：`0.5 格 → 0`、`1.5 格 → 2`、`2.5 格 → 2`。本 demo 三条断言分别钉住。

## 三、量化 / 反量化 / 伪量化

```
x_q  = clamp(round(x/scale) + zero_point, qmin, qmax)
x_dq = (x_q − zero_point) · scale
```

`FakeQuantize.forward` 有两个独立开关：`observer_enabled`（要不要更新 scale/zp）与 `fake_quant_enabled`（要不要伪量化）。后者为 0 时**整个模块原样透传**。

256 档网格在 `scale = 2/255, zp = 128` 下覆盖的是 `[−256/255, 1]` 而不是 `[−1, 1]`——负端多出一格，这是 `clamp` 之后 `zp` 与量程不能整除的自然结果。

## 四、三类 observer 的统计口径

| observer | 行为 |
| --- | --- |
| `MinMaxObserver` | running min/max，初值 `(inf, −inf)`；空张量直接返回 |
| `MovingAverageMinMaxObserver` | **首帧直接取**（`min==inf and max==−inf` 分支），之后 `m += c·(cur − m)` |
| `PerChannelMinMaxObserver` | 按 `ch_axis` 逐通道统计，返回一组 (scale, zp) |

同一串数据 `[−1,1]` 后接 `[−3,3]`、`c=0.5`：

```
MinMax          : (−3.0, 3.0)
MovingAvg c=0.5 : (−2.0, 2.0)
```

EMA 的结果**不是**历史极值，落在两次观测之间——这既是它对离群 batch 更鲁棒的原因，也是它可能低估真实量程的原因。

未跑过 observer 就去 `calculate_qparams`（仍是 `(inf, −inf)`）会走 `check_min_max_valid` 的 False 分支，返回 **scale=1.0、zero_point=0**（并 warn）。`min > max` 则直接 `AssertionError`。

## 五、HistogramObserver：为什么「裁剪」能降低误差

`_non_linear_param_search` 用一个 `stepsize=1e-5` 的双指针在分位数上推进：

```
while alpha < beta:
    l = 第一个使 cumsum[l] ≥ next_alpha·total 的桶
    r = 最后一个使 cumsum[r] ≤ next_beta·total 的桶
    哪一侧跳得远就动哪一侧（(l−start) > (end−r) ? 动 start : 动 end）
    若 norm 变大了就 break
```

误差度量 `_compute_quantization_error` 的关键是：**被排除在 `[start, end]` 之外的桶不会免费消失**，它们会被 `clamp` 到首尾档位上，因此离得越远贡献的 L2 误差越大（`norm = density·(end³ − begin³)/3`，即 x² 在区间上的积分）。所以搜索会在「裁掉尾部省下的分辨率」和「被裁样本被压到边界的代价」之间取平衡。

实测（bins=64, dst_nbins=256, 主体 `N(0.5, 0.05)`）：

| 数据 | 结果 |
| --- | --- |
| 主体 + 右端 200 个 `1.0` | `start=20, end=63` → 只裁左侧空区间，保留离群簇 |
| 主体 + 左端 200 个 `0.0` | `start=0, end=40` → 只裁右侧空区间，保留离群簇 |
| 主体 + 两端各 1 个离群点 | `start=0, end=62` → 只动 1 格就 break |
| 均匀分布 | `start=0, end=30` → 只动 1 格就 break |

也就是说：**离群点少的时候 PyTorch 的直方图校准几乎不裁剪**，它裁的是「空区间」而不是「离群点」——这与「histogram 校准能自动去掉 outlier」的直觉不同，除非离群样本占比足够大（源码的 `alpha/beta` 是**分位数**，不是数值阈值）。

## 六、代码结构

| 文件 | 说明 |
| --- | --- |
| `python/quant.py` | qmin/qmax 表 + `_calculate_qparams` 三分支 + 量化/伪量化 + 三类 observer + 直方图搜索 |
| `python/selfcheck_quant.py` | 85 条断言（实跑全绿） |
| `python/main.py` | 演示入口 |
| `go/quant.go` + `go/main.go` | Go 同题实现（无本机工具链，走机械核查） |

> Go 侧差异显式落地：`math.Round` 是「半数远离零」而 `torch.round` 是「半数取偶」，因此单独实现 `RoundHalfEven` 对齐。

运行：

```bash
cd python && python selfcheck_quant.py && python main.py
```

## 七、参考资料（实际读过）

- `pytorch/pytorch@main` — `torch/ao/quantization/observer.py`（80.4 KB）：`UniformQuantizationObserverBase` 的 `reduce_range` 弃用告警（245–251 行）、`_calculate_qparams` 全分支（349–427 行）、`MinMaxObserver.forward`（558–569 行）、`MovingAverageMinMaxObserver.forward`（668–683 行）、`PerChannelMinMaxObserver`（686–795 行）、`HistogramObserver` 的 `_get_norm` / `_compute_quantization_error` / `_non_linear_param_search`（1055–1192 行）
- `pytorch/pytorch@main` — `torch/ao/quantization/utils.py`（29.6 KB）：`check_min_max_valid`（414–442 行）、`calculate_qmin_qmax`（445–506 行）
- `pytorch/pytorch@main` — `torch/ao/quantization/fake_quantize.py`（23.5 KB）：`_is_per_channel/_is_symmetric_quant/_is_float_qparams`（50–67 行）、`FakeQuantize.forward` 的双开关（228–260 行）
