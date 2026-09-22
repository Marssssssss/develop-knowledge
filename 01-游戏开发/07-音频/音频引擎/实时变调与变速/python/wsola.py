"""信号级链路：重采样 + WSOLA 时间拉伸（pitchshift.py 的选项位与模式约束见同目录）。"""

import math

from pitchshift import semitones_to_pitch_scale


def resample_linear(x, ratio):
    """按 ratio 改变播放速率：时长 /ratio，音高 ×ratio。"""
    n = len(x)
    m = max(1, int(round(n / ratio)))
    out = []
    for i in range(m):
        pos = i * ratio
        i0 = int(math.floor(pos))
        frac = pos - i0
        a = x[i0] if i0 < n else 0.0
        b = x[i0 + 1] if i0 + 1 < n else 0.0
        out.append(a * (1 - frac) + b * frac)
    return out


def wsola_stretch(x, ratio, frame=2048, hop=1024, search=512):
    """WSOLA 时间拉伸：时长 ×ratio，音高不变。

    与朴素 OLA 的区别是每帧先在与上一帧最相似的偏移处对齐再拼接，
    否则帧边界的相位跳变会产生可听的调制（本 demo 首版就因此把 440 Hz 估成 101 Hz）。
    """
    if len(x) < frame:
        return list(x)
    out_len = int(round(len(x) * ratio))
    out = [0.0] * out_len
    norm = [0.0] * out_len
    syn_hop = max(1, int(round(hop * ratio)))
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / frame) for i in range(frame)]
    prev = None
    read = 0
    pos = 0
    while read + frame <= len(x) and pos + frame <= out_len:
        offset = read
        if prev is not None:
            lo = max(0, read - search)
            hi = min(len(x) - frame, read + search)
            best, offset = None, read
            for off in range(lo, hi + 1, 8):
                acc = 0.0
                for k in range(0, frame, 4):
                    acc += x[off + k] * prev[k]
                if best is None or acc > best:
                    best, offset = acc, off
        block = x[offset:offset + frame]
        for i, v in enumerate(block):
            out[pos + i] += v * window[i]
            norm[pos + i] += window[i]
        prev = block
        pos += syn_hop
        read += hop
    for i in range(out_len):
        if norm[i] > 1e-9:
            out[i] /= norm[i]
    return out


# 兼容旧名
ola_stretch = wsola_stretch


def pitch_shift(x, semitones, sample_rate=48000):
    """变调不变速：先按 1/p 变速（重采样），再按 p 拉伸还原时长。"""
    p = semitones_to_pitch_scale(semitones)
    fast = resample_linear(x, p)          # 时长 /p，音高 ×p
    return ola_stretch(fast, p)           # 时长 ×p 复原，音高不变


def time_stretch(x, time_ratio):
    """变速不变调。"""
    return ola_stretch(x, time_ratio)


def estimate_frequency(x, sample_rate, min_hz=40.0, max_hz=2000.0):
    """用自相关估计基频（对 OLA 的拼接瑕疵比过零率稳健）。"""
    seg_len = min(4096, max(512, len(x) // 2))
    start = max(0, (len(x) - seg_len) // 2)
    seg = x[start:start + seg_len]
    if len(seg) < 64:
        return 0.0
    mean = sum(seg) / len(seg)
    seg = [v - mean for v in seg]
    min_lag = max(2, int(sample_rate / max_hz))
    max_lag = min(len(seg) // 2, int(sample_rate / min_hz))
    best, best_lag = 0.0, min_lag
    for lag in range(min_lag, max_lag + 1):
        acc = 0.0
        for i in range(len(seg) - lag):
            acc += seg[i] * seg[i + lag]
        if acc > best:
            best, best_lag = acc, lag
    return sample_rate / best_lag
