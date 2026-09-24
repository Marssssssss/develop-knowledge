"""量化与校准 —— pytorch/pytorch 的 torch/ao/quantization/observer.py、fake_quantize.py、utils.py 逐行转写。

纯标准库。张量用一维 list 表示；qscheme 用字符串标签。
"""

import math

EPS = 2.220446049250313e-16  # torch.finfo(torch.float32).eps
INF = float("inf")


# ---------------------------------------------------------------- utils.py


def check_min_max_valid(min_val, max_val):
    """utils.check_min_max_valid：空 → False（warn）；inf/-inf → False；min>max → AssertionError。"""
    if min_val is None or max_val is None:
        return False
    if min_val == INF and max_val == -INF:
        return False
    if min_val > max_val:
        raise AssertionError("min %r should be less than max %r" % (min_val, max_val))
    return True


def calculate_qmin_qmax(quant_min, quant_max, has_customized_qrange, dtype, reduce_range):
    """utils.calculate_qmin_qmax：默认 8bit 档位表。"""
    if has_customized_qrange:
        qrange_len = quant_max - quant_min + 1
        if dtype in ("qint8", "int8") and not (0 < qrange_len <= 256):
            raise AssertionError("quantization range should be positive and not exceed 256")
        if reduce_range:
            return quant_min // 2, quant_max // 2
        return quant_min, quant_max
    if dtype in ("qint8", "int8"):
        return (-64, 63) if reduce_range else (-128, 127)
    if dtype in ("quint8", "uint8"):
        return (0, 127) if reduce_range else (0, 255)
    if dtype in ("qint32", "int32"):
        return -(2 ** 31), 2 ** 31 - 1
    if dtype == "uint16":
        return 0, 2 ** 16 - 1
    if dtype == "int16":
        return -(2 ** 15), 2 ** 15 - 1
    return 0, 15


# ---------------------------------------------------------------- observer: qparams


def calculate_qparams(min_val, max_val, qscheme, quant_min, quant_max,
                      dtype="quint8", eps=EPS, has_customized_qrange=False):
    """UniformQuantizationObserverBase._calculate_qparams 的三分支。"""
    if not check_min_max_valid(min_val, max_val):
        return 1.0, 0
    min_val_neg = min(min_val, 0.0)
    max_val_pos = max(max_val, 0.0)
    if qscheme in ("per_tensor_symmetric", "per_channel_symmetric"):
        max_val_pos = max(-min_val_neg, max_val_pos)
        scale = max_val_pos / (float(quant_max - quant_min) / 2)
        scale = max(scale, eps)
        if dtype in ("quint8", "uint8"):
            zp = (quant_min + quant_max) // 2 if has_customized_qrange else 128
        elif dtype == "uint16":
            zp = 2 ** 15
        else:
            zp = 0
    elif qscheme == "per_channel_affine_float_qparams":
        scale = (max_val - min_val) / float(quant_max - quant_min)
        scale = scale if scale > eps else 1.0
        zp = -1 * min_val / scale
    else:
        scale = (max_val_pos - min_val_neg) / float(quant_max - quant_min)
        scale = max(scale, eps)
        zp = quant_min - int(round(min_val_neg / scale))
        zp = max(quant_min, min(quant_max, zp))
    return scale, zp


# ---------------------------------------------------------------- 量化 / 反量化


def quantize_per_tensor(x, scale, zero_point, quant_min, quant_max):
    """x_q = clamp(round(x/scale) + zero_point, qmin, qmax)。"""
    out = []
    for v in x:
        q = int(round(v / scale)) + zero_point
        out.append(max(quant_min, min(quant_max, q)))
    return out


def dequantize(xq, scale, zero_point):
    return [(q - zero_point) * scale for q in xq]


def fake_quantize(x, scale, zero_point, quant_min, quant_max):
    """FakeQuantize.forward 的 fake_quant 分支：量化后立即反量化。"""
    return dequantize(quantize_per_tensor(x, scale, zero_point, quant_min, quant_max), scale, zero_point)


def fake_quantize_forward(x, scale, zero_point, quant_min, quant_max,
                          observer_enabled=True, fake_quant_enabled=True):
    """FakeQuantize.forward 的两个开关：observer 更新 scale/zp，fake_quant 决定要不要伪量化。"""
    if not fake_quant_enabled:
        return list(x)
    return fake_quantize(x, scale, zero_point, quant_min, quant_max)


# ---------------------------------------------------------------- observers


class MinMaxObserver:
    """MinMaxObserver：running min/max，初值 ±inf。"""

    def __init__(self, qscheme="per_tensor_affine", dtype="quint8",
                 quant_min=None, quant_max=None, eps=EPS, has_customized_qrange=False):
        self.min_val, self.max_val = INF, -INF
        self.qscheme, self.dtype = qscheme, dtype
        if quant_min is None or quant_max is None:
            self.quant_min, self.quant_max = calculate_qmin_qmax(
                quant_min or 0, quant_max or 255, has_customized_qrange, dtype, False)
        else:
            self.quant_min, self.quant_max = quant_min, quant_max
        self.eps = eps
        self.has_customized_qrange = has_customized_qrange

    def forward(self, x):
        if len(x) == 0:
            return x
        cur_min, cur_max = min(x), max(x)
        self.min_val = min(cur_min, self.min_val)
        self.max_val = max(cur_max, self.max_val)
        return x

    def calculate_qparams(self):
        return calculate_qparams(self.min_val, self.max_val, self.qscheme,
                                 self.quant_min, self.quant_max, self.dtype,
                                 self.eps, self.has_customized_qrange)


class MovingAverageMinMaxObserver(MinMaxObserver):
    """MovingAverageMinMaxObserver：首帧直接取，之后做 EMA。"""

    def __init__(self, averaging_constant=0.01, **kw):
        super().__init__(**kw)
        self.averaging_constant = averaging_constant

    def forward(self, x):
        if len(x) == 0:
            return x
        cur_min, cur_max = min(x), max(x)
        if self.min_val == INF and self.max_val == -INF:
            self.min_val, self.max_val = cur_min, cur_max
        else:
            c = self.averaging_constant
            self.min_val = self.min_val + c * (cur_min - self.min_val)
            self.max_val = self.max_val + c * (cur_max - self.max_val)
        return x


class PerChannelMinMaxObserver:
    """PerChannelMinMaxObserver：按 ch_axis 逐通道统计。"""

    def __init__(self, ch_axis=0, qscheme="per_channel_affine", dtype="quint8",
                 quant_min=0, quant_max=255, eps=EPS):
        self.ch_axis = ch_axis
        self.min_vals, self.max_vals = [], []
        self.qscheme, self.dtype = qscheme, dtype
        self.quant_min, self.quant_max = quant_min, quant_max
        self.eps = eps

    def forward(self, x):
        """x 为 [C][...]。"""
        for c, chan in enumerate(x):
            flat = _flatten(chan)
            if c >= len(self.min_vals):
                self.min_vals.append(min(flat))
                self.max_vals.append(max(flat))
            else:
                self.min_vals[c] = min(min(flat), self.min_vals[c])
                self.max_vals[c] = max(max(flat), self.max_vals[c])
        return x

    def calculate_qparams(self):
        return [
            calculate_qparams(lo, hi, self.qscheme, self.quant_min, self.quant_max, self.dtype, self.eps)
            for lo, hi in zip(self.min_vals, self.max_vals)
        ]


def _flatten(v):
    out = []
    for e in v:
        if isinstance(e, list):
            out.extend(_flatten(e))
        else:
            out.append(e)
    return out


# ---------------------------------------------------------------- HistogramObserver


class HistogramObserver:
    """HistogramObserver 的 _get_norm / _compute_quantization_error / _non_linear_param_search。"""

    def __init__(self, bins=2048, dtype="quint8", min_val=INF, max_val=-INF):
        self.bins = bins
        self.dtype = dtype
        self.dst_nbins = 2 ** 8 if dtype in ("quint8", "uint8", "qint8", "int8") else 2 ** 16
        self.min_val, self.max_val = min_val, max_val
        self.histogram = [0.0] * bins

    def fill(self, values):
        """把样本按 [min_val, max_val] 均匀分桶。"""
        span = self.max_val - self.min_val
        self.histogram = [0.0] * self.bins
        for v in values:
            idx = int((v - self.min_val) / span * self.bins)
            idx = max(0, min(self.bins - 1, idx))
            self.histogram[idx] += 1.0
        return self.histogram

    def _get_norm(self, delta_begin, delta_end, density):
        """norm = density * (end³ − begin³) / 3。"""
        return density * (delta_end ** 3 - delta_begin ** 3) / 3

    def _compute_quantization_error(self, next_start_bin, next_end_bin):
        bin_width = (self.max_val - self.min_val) / self.bins
        dst_bin_width = bin_width * (next_end_bin - next_start_bin + 1) / self.dst_nbins
        if dst_bin_width == 0.0:
            return 0.0
        norm = 0.0
        for src_bin in range(self.bins):
            src_bin_begin = (src_bin - next_start_bin) * bin_width
            src_bin_end = src_bin_begin + bin_width
            b0 = _clamp(math.floor(src_bin_begin / dst_bin_width), 0, self.dst_nbins - 1)
            b1 = _clamp(math.floor(src_bin_end / dst_bin_width), 0, self.dst_nbins - 1)
            c0 = (b0 + 0.5) * dst_bin_width
            density = self.histogram[src_bin] / bin_width
            norm += self._get_norm(src_bin_begin - c0, dst_bin_width / 2, density)
            norm += (b1 - b0 - 1) * self._get_norm(-dst_bin_width / 2, dst_bin_width / 2, density)
            c1 = b1 * dst_bin_width + dst_bin_width / 2
            norm += self._get_norm(-dst_bin_width / 2, src_bin_end - c1, density)
        return norm

    def _non_linear_param_search(self):
        bin_width = (self.max_val - self.min_val) / self.bins
        total = sum(self.histogram)
        csum = _cumsum(self.histogram)
        stepsize, alpha, beta = 1e-5, 0.0, 1.0
        start_bin, end_bin = 0, self.bins - 1
        norm_min = INF
        while alpha < beta:
            next_alpha = alpha + stepsize
            next_beta = beta - stepsize
            l, r = start_bin, end_bin
            while l < end_bin and csum[l] < next_alpha * total:
                l += 1
            while r > start_bin and csum[r] > next_beta * total:
                r -= 1
            next_start, next_end = start_bin, end_bin
            if (l - start_bin) > (end_bin - r):
                next_start = l
                alpha = next_alpha
            else:
                next_end = r
                beta = next_beta
            if next_start == start_bin and next_end == end_bin:
                continue
            norm = self._compute_quantization_error(next_start, next_end)
            if norm > norm_min:
                break
            norm_min = norm
            start_bin, end_bin = next_start, next_end
        return (self.min_val + bin_width * start_bin,
                self.min_val + bin_width * (end_bin + 1),
                start_bin, end_bin)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _cumsum(xs):
    out, acc = [], 0.0
    for v in xs:
        acc += v
        out.append(acc)
    return out
