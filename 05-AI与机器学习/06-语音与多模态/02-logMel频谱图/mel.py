"""log-Mel 频谱图（模型层）

两处权威来源，按原文/官方源码逐条实现：

A. librosa 官方实现（`librosa/filters.py` 的 `mel()`、`librosa/core/convert.py`
   的 `hz_to_mel()` / `mel_to_hz()`），Slaney 口径（htk=False、norm='slaney'）：
   - f_min=0.0, f_sp=200/3；min_log_hz=1000.0，min_log_mel=(1000-0)/(200/3)
   - logstep = ln(6.4)/27
   - f>=1000: mel = min_log_mel + ln(f/1000)/logstep
   - 滤波器：mel_f = mel_to_hz(linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels+2))
     lower = (mel_f[i] - fftfreqs)/fdiff[i] 取负后与 upper 取 min，再与 0 取 max
   - slaney 归一化：enorm = 2/(mel_f[2:n_mels+2] − mel_f[:n_mels])

B. OpenAI Whisper 官方实现（`whisper/audio.py`，jsDelivr 取 main 分支原文）：
   - SAMPLE_RATE=16000, N_FFT=400, HOP_LENGTH=160, CHUNK_LENGTH=30
   - N_SAMPLES=480000, N_FRAMES=exact_div(N_SAMPLES, HOP_LENGTH)=3000
   - FRAMES_PER_SECOND=100, TOKENS_PER_SECOND=50, N_SAMPLES_PER_TOKEN=320
   - window = torch.hann_window(N_FFT)；stft[..., :-1] 丢掉最后一帧
   - magnitudes = |STFT|**2（功率谱，不是幅度谱）
   - filters = librosa.filters.mel(sr=16000, n_fft=400, n_mels=80/128)
   - log_spec = clamp(mel, min=1e-10).log10()
   - log_spec = maximum(log_spec, log_spec.max() - 8.0)
   - log_spec = (log_spec + 4.0) / 4.0
"""

import math
import cmath

# ---- A. Slaney mel 标度与滤波器组（librosa 官方口径） ----

F_MIN = 0.0
F_SP = 200.0 / 3
MIN_LOG_HZ = 1000.0
MIN_LOG_MEL = (MIN_LOG_HZ - F_MIN) / F_SP          # = 15.0
LOGSTEP = math.log(6.4) / 27.0


def hz_to_mel(f, htk=False):
    if htk:
        return 2595.0 * math.log10(1.0 + f / 700.0)
    if f < MIN_LOG_HZ:
        return (f - F_MIN) / F_SP
    return MIN_LOG_MEL + math.log(f / MIN_LOG_HZ) / LOGSTEP


def mel_to_hz(m, htk=False):
    if htk:
        return 700.0 * (10.0 ** (m / 2595.0) - 1.0)
    if m < MIN_LOG_MEL:
        return F_MIN + F_SP * m
    return MIN_LOG_HZ * math.exp(LOGSTEP * (m - MIN_LOG_MEL))


def mel_frequencies(n, fmin, fmax, htk=False):
    lo, hi = hz_to_mel(fmin, htk), hz_to_mel(fmax, htk)
    if n == 1:
        return [mel_to_hz(lo, htk)]
    return [mel_to_hz(lo + (hi - lo) * i / (n - 1), htk) for i in range(n)]


def fft_frequencies(sr, n_fft):
    return [sr * k / n_fft for k in range(1 + n_fft // 2)]


def mel_filterbank(sr, n_fft, n_mels=128, fmin=0.0, fmax=None, norm="slaney"):
    """librosa.filters.mel 的等价实现（默认 slaney）。返回 (weights, mel_f)。"""
    if fmax is None:
        fmax = float(sr) / 2.0
    n_bins = 1 + n_fft // 2
    fftfreqs = fft_frequencies(sr, n_fft)
    mel_f = mel_frequencies(n_mels + 2, fmin, fmax)
    fdiff = [mel_f[i + 1] - mel_f[i] for i in range(len(mel_f) - 1)]
    weights = []
    for i in range(n_mels):
        row = []
        for f in fftfreqs:
            lower = (mel_f[i] - f) / fdiff[i] * -1.0   # -ramps[i]/fdiff[i]
            upper = (mel_f[i + 2] - f) / fdiff[i + 1]  # ramps[i+2]/fdiff[i+1]
            row.append(max(0.0, min(lower, upper)))
        weights.append(row)
    if norm == "slaney":
        for i in range(n_mels):
            enorm = 2.0 / (mel_f[i + 2] - mel_f[i])
            weights[i] = [w * enorm for w in weights[i]]
    return weights, mel_f


# ---- B. STFT（onesided / center=True / periodic hann） ----

def hann_window(n):
    return [0.5 * (1.0 - math.cos(2.0 * math.pi * i / n)) for i in range(n)]


def stft_power(sig, n_fft, hop):
    """返回 (frames, n_fft//2+1) 的功率谱，与 torch.stft(center=True, onesided) 同口径。"""
    pad = n_fft // 2
    n_frames = 1 + (len(sig) + 2 * pad - n_fft) // hop
    win = hann_window(n_fft)
    out = []
    for t in range(n_frames):
        start = t * hop - pad
        frame = []
        for i in range(n_fft):
            j = start + i
            frame.append(sig[j] if 0 <= j < len(sig) else 0.0)
        row = []
        for k in range(n_fft // 2 + 1):
            acc = 0j
            for i in range(n_fft):
                ang = -2.0 * math.pi * k * i / n_fft
                acc += frame[i] * win[i] * cmath.exp(1j * ang)
            row.append(abs(acc) ** 2)
        out.append(row)
    return out


def stft_frame_count(n_samples, n_fft, hop):
    """torch.stft(center=True) 的帧数：1 + (N + n_fft - n_fft) // hop。"""
    return 1 + (n_samples + 2 * (n_fft // 2) - n_fft) // hop


# ---- C. Whisper 的 log-Mel 管线 ----

SAMPLE_RATE = 16000
N_FFT = 400
HOP_LENGTH = 160
CHUNK_LENGTH = 30
N_SAMPLES = CHUNK_LENGTH * SAMPLE_RATE          # 480000
N_FRAMES = N_SAMPLES // HOP_LENGTH              # 3000
N_SAMPLES_PER_TOKEN = HOP_LENGTH * 2            # 320
FRAMES_PER_SECOND = SAMPLE_RATE // HOP_LENGTH   # 100
TOKENS_PER_SECOND = SAMPLE_RATE // N_SAMPLES_PER_TOKEN   # 50


def log_mel_spectrogram(power, filters):
    """严格照抄 whisper/audio.py 的三步后处理。power: (frames, n_bins)"""
    mel = [[sum(filters[m][b] * row[b] for b in range(len(row)))
            for m in range(len(filters))] for row in power]
    log_spec = [[math.log10(max(v, 1e-10)) for v in row] for row in mel]
    gmax = max(max(row) for row in log_spec)
    log_spec = [[max(v, gmax - 8.0) for v in row] for row in log_spec]
    return [[(v + 4.0) / 4.0 for v in row] for row in log_spec]


def load_scale(n):
    """whisper 的 load_audio 用 int16 PCM：除以 32768.0 归一化。"""
    return 1.0 / 32768.0
