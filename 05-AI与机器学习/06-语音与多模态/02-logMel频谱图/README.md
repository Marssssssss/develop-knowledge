# 458 · log-Mel 频谱图（STFT → Mel 滤波器组 → log）

## 简介

语音模型不吃波形本身，而吃 **log-Mel 频谱图**：一张 `n_mels × 帧数` 的二维图。它把「宽 400 点、每秒 100 帧」的原始频谱压缩成 80 个频带，并把能量压进对数刻度，让**人耳感知上等距**的频率在图上也是等距的。

本 demo 用两条**官方一手来源**把整条管线拆开实现：

- **滤波器组**照抄 `librosa.filters.mel` 的官方实现（Slaney 口径，`htk=False`、`norm='slaney'`）；
- **整条特征管线**照抄 OpenAI Whisper 官方 `whisper/audio.py` 的硬编码常量与三步后处理。

之所以选 Whisper，是因为它把每个数字都写死在源码里（`N_FFT=400`、`HOP_LENGTH=160`、`clamp(min=1e-10)`、`max - 8.0`、`(x+4)/4`），正好可以逐条核对，不必靠猜。

## 原理详解

### 1. STFT：波形 → 功率谱

Whisper 的配置（全部来自 `whisper/audio.py` 顶部注释）：

```
SAMPLE_RATE  = 16000
N_FFT        = 400     → 25 ms 窗
HOP_LENGTH   = 160     → 10 ms 帧移
CHUNK_LENGTH = 30      → 30 秒 = 480000 样本
N_FRAMES     = 480000 / 160 = 3000
FRAMES_PER_SECOND     = 16000 / 160 = 100
N_SAMPLES_PER_TOKEN   = 160 * 2 = 320
TOKENS_PER_SECOND     = 16000 / 320 = 50
```

三处实现细节容易错：

1. **窗是 Hann**（`torch.hann_window(N_FFT)`），周期窗，`w[n] = 0.5(1 − cos(2πn/N))`；
2. **`magnitudes = |STFT| ** 2`**，是**功率谱**不是幅度谱。少这次平方，后面 log 的动态范围会差一半；
3. **`stft[..., :-1]`**：`center=True` 时首尾各补 `N_FFT//2`，480000 样本算出 **3001** 帧，Whisper 丢掉最后一帧得到 **3000**。这一步很容易漏，漏了编码器输入就差一帧。

帧数公式（`center=True`）：

```
n_frames = 1 + (N + 2·(n_fft//2) − n_fft) / hop = 1 + 480000/160 = 3001
```

另外 `load_audio` 用 ffmpeg 解成 `s16le` 后**除以 32768.0** 归一化到 `[-1, 1]`。

### 2. Mel 标度：Slaney vs HTK

`librosa` 默认 **Slaney**（`htk=False`），是一段**线性 + 一段对数**的分段函数：

```
f < 1000 Hz :  mel = f / (200/3)
f ≥ 1000 Hz:  mel = 15 + ln(f/1000) / (ln(6.4)/27)
```

其中 `f_sp = 200/3`、`min_log_hz = 1000.0`、`min_log_mel = 1000·3/200 = 15`、`logstep = ln(6.4)/27`。1000 Hz 以下线性段斜率刻意取成让两段在 1000 Hz 处接上。

HTK 口径（另一套，Kaldi / HTK 系常用）是纯对数：`mel = 2595·log₁₀(1 + f/700)`。两套在 1000 Hz 处就已经不同，混用会让滤波器组整体错位。librosa docstring 自带的例子 `hz_to_mel(60) ≈ 0.9`、`hz_to_mel([110,220,440]) ≈ [1.65, 3.3, 6.6]` 本 demo 都做了断言。

### 3. 三角滤波器组

`librosa.filters.mel` 的构造：

```
mel_f = mel_to_hz(linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2))
lower = (mel_f[i]   − fftfreqs) / (mel_f[i+1] − mel_f[i])  取负
upper = (mel_f[i+2] − fftfreqs) / (mel_f[i+2] − mel_f[i+1])
weights[i] = max(0, min(lower, upper))
```

注意三点：

- `mel_f` 取 **n_mels + 2** 个点，第 `i` 个滤波器横跨 `[mel_f[i], mel_f[i+2]]`，顶点在 `mel_f[i+1]`。相邻滤波器**重叠一半**；
- `fmax` 不传时取 `sr/2`（16 kHz → 8000 Hz）；
- **`norm='slaney'` 会再乘 `2/(mel_f[i+2] − mel_f[i])`**，即按频带宽度做面积归一化，目的是「每个通道大致等能量」。不归一化时三角形峰值朝 1.0。

> **坑**：离散 FFT bin 未必正好落在三角形顶点上，所以未归一化时峰值是 **≤ 1** 而不是恒等于 1。本 demo 断言 `峰值 ≤ 1`，并用 `n_fft` 从 400 提到 4000 后峰值趋近 1（`B9`）来证明这是采样问题而不是公式问题 —— 一开始写成「峰值 == 1」全部失败。

输出形状 `(n_mels, 1 + n_fft//2)`，Whisper 的 80 带配置即 **(80, 201)**；`n_fft=400`、`sr=16000` 时频率分辨率 **40 Hz**（`16000/400`），最高 bin 8000 Hz。

Mel 带**低频密、高频疏**：本 demo 实测第 3 带与第 79 带的宽度相差 2 倍以上。这正是 mel 的目的 —— 低频分辨音高与共振峰，高频只保留粗糙的能量轮廓。

### 4. Whisper 的三步后处理

```python
log_spec = torch.clamp(mel_spec, min=1e-10).log10()   # ① 防 log(0)
log_spec = torch.maximum(log_spec, log_spec.max() - 8.0)   # ② 动态范围压到 8
log_spec = (log_spec + 4.0) / 4.0                     # ③ 归一到约 [0, 1]
```

- ① `log10` 而不是 `ln`；clamp 下限 `1e-10` 保证静音段得到 −10 而不是 −inf；
- ② 以**整段最大值**为锚，砍掉低于 `max − 8` 的部分。8 个 log₁₀ 单位 = **80 dB** 动态范围，超出的一律削平，这样安静的录音不会被噪声底填满；
- ③ 平移缩放，`−4 → 0`，把数值拉到大致 `[0, 1]`。

本 demo 的断言：① 输出动态范围 ≤ 8/4 = 2（`E2`）；② 幅度翻倍的 1 kHz 正弦，其未触顶的 mel 带恰好 `+log₁₀(4)/4 ≈ +0.0753`（`E4`）；③ 全零输入每帧都等于 `(−10+4)/4 = −1.5`（`E5`）。

## 对比：两种归一化与两种 mel 口径

| 配置项 | 取值 | 影响 |
| --- | --- | --- |
| `norm='slaney'`（默认） | 每带乘 `2/(f_hi−f_lo)` | 各带等能量，高频带被放大 |
| `norm=None` | 三角形峰值朝 1.0 | 高频带数值偏小 |
| `norm=<数值 p>` | `util.normalize(p 范数)` | 每带单位 p 范数 |
| `htk=False`（Slaney） | 分段线性+对数 | librosa / torchaudio 默认 |
| `htk=True` | `2595·log₁₀(1+f/700)` | HTK / Kaldi 系默认 |

## 环境

Python 3.13（仅用标准库 `math` / `cmath` / `random`）；Go 1.20+（仅用标准库，含 `math/cmplx`）。

## 运行方式

```bash
cd 05-AI与机器学习/06-语音与多模态/02-logMel频谱图
python selfcheck_mel.py    # 45 条断言实跑
go run mel.go
```

## 关键代码

```python
def mel_filterbank(sr, n_fft, n_mels=128, fmin=0.0, fmax=None, norm="slaney"):
    fftfreqs = fft_frequencies(sr, n_fft)
    mel_f = mel_frequencies(n_mels + 2, fmin, fmax)   # 比 n_mels 多 2 个端点
    for i in range(n_mels):
        for f in fftfreqs:
            lower = (mel_f[i] - f) / fdiff[i] * -1.0
            upper = (mel_f[i + 2] - f) / fdiff[i + 1]
            row.append(max(0.0, min(lower, upper)))
    if norm == "slaney":
        weights[i] *= 2.0 / (mel_f[i + 2] - mel_f[i])
```

## 性能边界

- 直接的 DFT 是 `O(n_fft²)` per frame，本 demo 只用于**小信号验证**（1600 样本 / 11 帧）。真实 30 秒音频应走 FFT：`O(T·n_fft·log n_fft)`。
- 30 秒音频的频谱是 `3000 × 201`，经 80 带滤波器组后 `3000 × 80`；编码器再按 `input_stride = N_FRAMES / n_audio_ctx = 2` 下采样成 1500 个位置。
- 滤波器组是**常量矩阵**，Whisper 直接把 `librosa.filters.mel(sr=16000, n_fft=400, n_mels=80/128)` 的结果存成 `assets/mel_filters.npz` 打包发布，运行时不依赖 librosa —— 目的正是「decoupling librosa dependency」（源码注释原话）。
- 数值：`log10` 前必须 clamp，否则静音段产生 `-inf`，后续 `(x+4)/4` 会把 `-inf` 传遍整张图。

## 注意事项与常见坑

1. **功率谱 vs 幅度谱**：Whisper 取 `|STFT|²`，少平方会让 log 后的动态范围减半（30 dB 当 15 dB 用）。
2. **`stft[..., :-1]` 别漏**：`center=True` 是 3001 帧，`N_FRAMES=3000` 是丢掉最后一帧的结果。
3. **`mel_f` 长度是 `n_mels+2`**，写成 `n_mels` 会让最后一个带没有上边界。
4. **三角形峰值不恒等于 1**，断言要写 `≤ 1`。
5. **Slaney 与 HTK 不能混用**，两者在 1000 Hz 处就分叉。
6. **动态范围钳位以整段最大值为锚**，所以同一段音频在不同增益下裁剪位置不同（增益提高 10 dB，被削平的部分也跟着变）。
7. `fmax=None` 时取 `sr/2`；若重采样到 8 kHz 却仍按 16 kHz 构造滤波器组，滤波器会伸到 Nyquist 之外，librosa 会警告 "Empty filters detected"。

## 参考资料

- OpenAI Whisper 官方实现 —— `whisper/audio.py`（本 demo 全部硬编码常量与三步后处理逐行照抄）：<https://github.com/openai/whisper/blob/main/whisper/audio.py>（本轮实际读取走 jsDelivr：`cdn.jsdelivr.net/gh/openai/whisper@main/whisper/audio.py`）
- librosa 官方文档 `librosa.filters.mel`（参数默认值 `n_mels=128 / fmin=0.0 / fmax=None→sr/2 / htk=False / norm='slaney'`，返回形状 `(n_mels, 1+n_fft/2)`）：<https://librosa.org/doc/0.10.2/generated/librosa.filters.mel.html>
- librosa 官方源码 `librosa/filters.py`（`mel()` 的三角构造与 slaney 归一化 `enorm = 2.0/(mel_f[2:n_mels+2] − mel_f[:n_mels])`）与 `librosa/core/convert.py`（`hz_to_mel()` 的 `f_sp=200/3`、`min_log_hz=1000`、`logstep=ln(6.4)/27`）。
- A. Radford et al. *Robust Speech Recognition via Large-Scale Weak Supervision*（Whisper 论文，log-Mel 作为编码器输入）。
