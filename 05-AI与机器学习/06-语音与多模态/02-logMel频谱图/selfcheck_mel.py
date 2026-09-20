"""log-Mel 频谱图 demo 自检：全部断言实跑。"""
import math
import random

from mel import (
    F_SP, MIN_LOG_MEL, LOGSTEP, hz_to_mel, mel_to_hz, mel_frequencies,
    fft_frequencies, mel_filterbank, hann_window, stft_power, stft_frame_count,
    SAMPLE_RATE, N_FFT, HOP_LENGTH, N_SAMPLES, N_FRAMES,
    N_SAMPLES_PER_TOKEN, FRAMES_PER_SECOND, TOKENS_PER_SECOND,
    log_mel_spectrogram,
)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append(f"{label}: {detail}")
        print(f"  FAIL {label} {detail}")


def close(a, b, eps=1e-9):
    return abs(a - b) <= eps


print("=== A. Slaney mel 标度（librosa convert.py 口径） ===")
check("A1 min_log_mel = 15", close(MIN_LOG_MEL, 15.0), f"{MIN_LOG_MEL}")
check("A2 f_sp = 200/3", close(F_SP, 200.0 / 3))
check("A3 logstep = ln(6.4)/27", close(LOGSTEP, math.log(6.4) / 27.0))
# librosa 官方 docstring 给的例子
check("A4 hz_to_mel(60) ≈ 0.9", close(hz_to_mel(60), 0.9, 1e-9), f"{hz_to_mel(60)}")
check("A5 hz_to_mel(110) ≈ 1.65", close(hz_to_mel(110), 1.65, 1e-9))
check("A6 hz_to_mel(220) ≈ 3.3", close(hz_to_mel(220), 3.3, 1e-9))
check("A7 hz_to_mel(440) ≈ 6.6", close(hz_to_mel(440), 6.6, 1e-9))
check("A8 hz_to_mel(1000) == 15", close(hz_to_mel(1000), 15.0))
# 1000Hz 以下线性、以上对数
check("A9 1000Hz 处两段接得上", close(hz_to_mel(999.999999), 15.0, 1e-6))
check("A10 线性段 300Hz", close(hz_to_mel(300), 300 * 3 / 200, 1e-9))
check("A11 对数段 4000Hz",
      close(hz_to_mel(4000), 15.0 + math.log(4.0) / LOGSTEP, 1e-9))
# 往返
bad = [f for f in (50, 200, 999, 1000, 1001, 3000, 8000)
       if not close(mel_to_hz(hz_to_mel(f)), f, 1e-6)]
check("A12 往返一致", not bad, f"{bad}")
# HTK 口径对照（论文里常见的另一套）
check("A13 htk 1000Hz ≠ slaney",
      not close(hz_to_mel(1000, htk=True), hz_to_mel(1000), 1e-6))
check("A14 htk 1000Hz = 2595*log10(1+1000/700)",
      close(hz_to_mel(1000, htk=True), 2595.0 * math.log10(1 + 1000 / 700.0), 1e-9))

print("=== B. 滤波器组形状与三角支撑（librosa filters.py 口径） ===")
SR, NF, NM = 16000, 400, 80
W, mel_f = mel_filterbank(SR, NF, NM)
check("B1 形状 (n_mels, 1+n_fft//2)", len(W) == NM and len(W[0]) == 1 + NF // 2,
      f"{len(W)}x{len(W[0])}")
check("B2 频率点数 201", len(W[0]) == 201)
fftf = fft_frequencies(SR, NF)
check("B3 fft 频率分辨率 40Hz", close(fftf[1] - fftf[0], 40.0))
check("B4 nyquist = 8000", close(fftf[-1], 8000.0))
# 三角支撑：只在 [mel_f[i], mel_f[i+2]] 之间非零
viol = 0
for i in range(NM):
    for b, v in enumerate(W[i]):
        if v > 0 and not (mel_f[i] - 1e-9 <= fftf[b] <= mel_f[i + 2] + 1e-9):
            viol += 1
check("B5 三角支撑不越界", viol == 0, f"{viol}")
# 峰值落在离 mel_f[i+1] 最近的 bin
peak_bad = 0
for i in range(NM):
    pk = max(range(len(W[i])), key=lambda b: W[i][b])
    want = min(range(len(fftf)), key=lambda b: abs(fftf[b] - mel_f[i + 1]))
    if pk != want:
        peak_bad += 1
check("B6 峰值在中心频率附近", peak_bad == 0, f"{peak_bad} 行不一致")
# 离散 bin 未必正好落在三角形顶点上，所以未归一化的峰值 ≤ 1 而非恒等于 1
W_raw, _ = mel_filterbank(SR, NF, NM, norm=None)
over = sum(1 for i in range(NM) if max(W_raw[i]) > 1.0 + 1e-9)
check("B7 未归一化峰值 ≤ 1", over == 0, f"{over} 行 >1")
# slaney 归一化：每个元素都等于原始值 × 2/(mel_f[i+2]−mel_f[i])
nbad = 0
for i in range(NM):
    e = 2.0 / (mel_f[i + 2] - mel_f[i])
    if any(not close(W[i][b], W_raw[i][b] * e, 1e-9) for b in range(len(W[i]))):
        nbad += 1
check("B8 slaney = 原值 × 2/(mel_f[i+2]−mel_f[i])", nbad == 0, f"{nbad}")
# 提高 FFT 分辨率后峰值趋近 1（证明 ≤1 是采样而非公式问题）
W_hi, _ = mel_filterbank(SR, 4000, NM, norm=None)
lo_peak = min(max(r) for r in W_raw)
hi_peak = min(max(r) for r in W_hi)
check("B9 n_fft 400→4000 峰值更接近 1", hi_peak > lo_peak and hi_peak > 0.9,
      f"{lo_peak:.4f} → {hi_peak:.4f}")
check("B10 每行都是单峰(先升后降)", all(
    (lambda r: all(r[j] <= r[j + 1] + 1e-12 for j in range(pk)) and
     all(r[j] >= r[j + 1] - 1e-12 for j in range(pk, len(r) - 1)))(row)
    for row, pk in ((row, max(range(len(row)), key=lambda b: row[b])) for row in W_raw)))
# mel 带宽随频率变宽
w_low = mel_f[3] - mel_f[2]
w_high = mel_f[NM - 1] - mel_f[NM - 2]
check("B9 高频 mel 带宽更宽", w_high > w_low * 2, f"{w_low} vs {w_high}")

print("=== C. Whisper 常量（audio.py 硬编码） ===")
check("C1 SAMPLE_RATE 16000", SAMPLE_RATE == 16000)
check("C2 N_FFT 400", N_FFT == 400)
check("C3 HOP_LENGTH 160", HOP_LENGTH == 160)
check("C4 N_SAMPLES 480000", N_SAMPLES == 480000)
check("C5 N_FRAMES 3000", N_FRAMES == 3000)
check("C6 N_SAMPLES_PER_TOKEN 320", N_SAMPLES_PER_TOKEN == 320)
check("C7 FRAMES_PER_SECOND 100", FRAMES_PER_SECOND == 100)
check("C8 TOKENS_PER_SECOND 50", TOKENS_PER_SECOND == 50)
# 30 秒音频的帧数：center=True 得 3001，audio.py 取 [..., :-1] 得 3000
nf_center = stft_frame_count(N_SAMPLES, N_FFT, HOP_LENGTH)
check("C9 center=True 帧数 3001", nf_center == 3001, f"{nf_center}")
check("C10 丢最后一帧后 3000 == N_FRAMES", nf_center - 1 == N_FRAMES)

print("=== D. 小信号 STFT 实测 ===")
random.seed(11)
sr = 16000
sig = [math.sin(2 * math.pi * 1000 * t / sr) for t in range(1600)]  # 0.1s @1kHz
P = stft_power(sig, 400, 160)
check("D1 帧数公式对得上", len(P) == stft_frame_count(1600, 400, 160),
      f"{len(P)} vs {stft_frame_count(1600,400,160)}")
check("D2 频点数 201", len(P[0]) == 201)
peak_bin = max(range(201), key=lambda b: P[len(P) // 2][b])
check("D3 1kHz 峰值落在 bin 25 (25*40Hz)", peak_bin == 25, f"bin={peak_bin}")
# hann 窗：端点为 0，中点为 1
w = hann_window(400)
check("D4 hann 端点为 0", close(w[0], 0.0))
check("D5 hann 中点 = 1", close(w[200], 1.0))

print("=== E. Whisper 三步后处理 ===")
filters80, _ = mel_filterbank(16000, 400, 80)
lm = log_mel_spectrogram(P, filters80)
check("E1 输出形状 (frames, n_mels)",
      len(lm) == len(P) and len(lm[0]) == 80, f"{len(lm[0])}")
# 动态范围被压到 8 个 log10 单位（= 80 dB）以内；除以 4 后即 ≤ 2
rng = max(max(r) for r in lm) - min(min(r) for r in lm)
check("E2 动态范围 ≤ 8/4 = 2", rng <= 2.0 + 1e-9, f"{rng}")
# 1kHz 能量集中在包含 1000Hz 的 mel 频带
mel_f80 = mel_frequencies(82, 0.0, 8000.0)
band = max(range(80), key=lambda m: lm[len(lm) // 2][m])
check("E3 mel 峰值带覆盖 1000Hz",
      mel_f80[band] <= 1000.0 <= mel_f80[band + 2],
      f"band={band} 区间[{mel_f80[band]:.1f},{mel_f80[band+2]:.1f}]")
# 幅度翻倍 → 功率 ×4 → log10 +0.30103 → 除以 4 后 +0.07526
sig2 = [2.0 * v for v in sig]
lm2 = log_mel_spectrogram(stft_power(sig2, 400, 160), filters80)
mid = len(lm) // 2
d = [lm2[mid][m] - lm[mid][m] for m in range(80)]
check("E4 未触顶的带 +0.0753",
      all((close(x, math.log10(4.0) / 4, 1e-6) or x == 0.0) for x in d),
      f"{max(d)}")
# 全静音：clamp 到 1e-10 → log10 = -10
sil = [0.0] * 1600
lms = log_mel_spectrogram(stft_power(sil, 400, 160), filters80)
check("E5 静音被 clamp 到 log10(1e-10) 的下游值",
      all(close(v, (-10.0 + 4.0) / 4.0, 1e-9) for v in lms[0]),
      f"{lms[0][0]}")

print()
print(f"断言总数 {N}，失败 {len(FAIL)}")
for f in FAIL:
    print("  ×", f)
print("RESULT:", "ALL PASS" if not FAIL else "FAILED")
