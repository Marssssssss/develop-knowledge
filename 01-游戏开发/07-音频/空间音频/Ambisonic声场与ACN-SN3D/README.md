# Ambisonic 声场：ACN 通道序、SN3D 归一化与一阶编解码

## 一句话

把「一个来自某方向的平面波」写成一组球谐系数（B 格式），再把这组系数摊到扬声器/虚拟扬声器上 —— 关键是**分量排谁在前（ACN）**和**每个分量乘多少（SN3D）**，两者都写在 Google 的 Spatial Audio RFC 里，可由公式直接推导而不必查表。

## 一、通道序 ACN

原文：`n = l * (l + 1) + m`（`l` 为次数 degree，`m` 为阶数 order，取值 `-l..l`）。

| ACN | (l, m) | 一阶惯用名 | SN3D |
| --- | --- | --- | --- |
| 0 | (0, 0) | **W**（全向） | 1 |
| 1 | (1, -1) | **Y**（左右） | 1 |
| 2 | (1, 0) | **Z**（上下） | 1 |
| 3 | (1, 1) | **X**（前后） | 1 |

注意一阶的 ACN 序是 **W, Y, Z, X**，不是直觉上的 W, X, Y, Z —— 这正是 `channel_map` 存在的理由。

## 二、归一化 SN3D

原文公式（`δ(m)` 为 Kronecker delta）：

```
N(l, m) = sqrt( (2 - δ(m)) * (l - m)! / (l + m)! )
```

`m` 取绝对值参与阶乘（`l - (-1)` 会算出 `(l+1)!`，是本 demo 首跑就踩到的坑）。
本 demo 额外给出 `N3D = SN3D * sqrt((2l+1)/4π)`（标准球谐正交归一化关系，**属本 demo 口径**，原文只定义 SN3D）：`W` 的 N3D 值为 `1/√(4π) ≈ 0.282095`。

## 三、球谐分量与方向约定

原文：分量 = `N(l, |m|) · P(l, |m|, sin E) · T(m, A)`，其中 `P` 是**不带 Condon-Shortley 相位**的缔合勒让德多项式，`T(m, x)` 在 `m < 0` 时为 `sin(-m·x)`、否则 `cos(m·x)`。

方向约定（原文附录）：`A = 0` 正前方，`A ∈ (0, π/2)` 左前象限；`E = 0` 水平面，`E ∈ (0, π/2]` 上方。

自检按此约定钉住：`X(正前) = 1`、`Y(正左) = 1`、`Y(正右) = -1`、`Z(正上) = 1`、`X(正后) = -1`。

## 四、可验证的恒等式：Σ Y² = order + 1

SN3D 下对**任意方向**都有 `Σ_n Y_n² = order + 1`（球谐加法定理的推论，N3D 下为 `(order+1)²/(4π)`）。本 demo 在 4 个随机方向、1 阶与 2 阶上各断言一次，是验证「归一化系数写对没有」最锋利的一条性质 —— 系数写错时方向语义可能仍对，但这个和一定不对。

## 五、编解码

- 编码：平面波 → `B[n] = gain · Y_n(E, A)`，按 ACN 序输出。
- 采样解码（**本 demo 口径**：`1/K` 加权）：`out_i = (1/K) · Σ_n B[n] · Y_n(扬声器方向)`。
- 一阶 + 4 扬声器水平环实测：命中方向 `0.5`、侧向 `0.25`（恰好 −6 dB）、背向 `0.0`。即采样解码不做伪逆补偿时**不是**理想的方向性重建，这一点与「Σ Y² = 2」同源。

## 六、channel_map 的语义

原文：轨道里存的是分量序列，`channel_map[i]` 给出 **ACN 分量 i 落在哪个轨道通道**。

| 轨道布局 | channel_map |
| --- | --- |
| W, X, Y, Z | `0, 2, 3, 1`（原文示例） |
| W, Y, Z, X | `0, 1, 2, 3`（原文示例） |
| W, Y, Z, X, L, R（head-locked 立体声） | `0, 1, 2, 3, 4, 5`（原文示例） |

> **原文第三例不自洽**：原文称布局 `L, R, W, Y, Z, X` 对应 `channel_map = 4, 5, 0, 1, 2, 3`。按与前两例相同的语义重算，应为 `2, 3, 4, 5, 0, 1`（W 在通道 2）。本 demo 断言重算值并把差异记在自检里，不照抄原文。

## 七、SA3D 盒

`SA3D` 放在 MP4 的采样描述盒内（`mp4a` → `esds` → `SA3D`），大端：

```
version(8) ambisonic_type(8) ambisonic_order(32) ambisonic_channel_ordering(8)
ambisonic_normalization(8) num_channels(32) channel_map[num_channels](32 each)
```

`ambisonic_type = 0` periphonic、`ordering = 0` ACN、`normalization = 0` SN3D。一阶 4 通道示例盒共 `12 + 4×4 = 28` 字节，自检做了往返与长度不符的负向断言。

> 原文正文提到 `head_locked_stereo` 是 `ambisonic_type` 里的 1 比特标志，但语法块把该字段声明为 `unsigned int(8)`。本 demo 按语法块**整字节**读取，不做位拆分。

## 八、运行

```bash
cd python && python selfcheck_ambisonic.py   # 68 断言全绿
python main.py
```

Go 侧（`go/ambisonic.go`）为同口径移植，本机无 Go 工具链，走人工审查 + 括号/结构静态检查。

## 参考资料（本 README 实读）

- Google《Spatial Audio RFC》— `https://github.com/google/spatial-media/blob/master/docs/spatial-audio-rfc.md`（ACN 公式、SN3D 公式、球谐定义与方向约定、通道数与 order 关系、SA3D 盒语法与三个 channel_map 示例）
