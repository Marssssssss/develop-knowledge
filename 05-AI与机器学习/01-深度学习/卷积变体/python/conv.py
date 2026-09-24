"""卷积变体 —— pytorch/pytorch torch/nn/modules/conv.py 逐行转写（Conv2d / ConvTranspose2d / _ConvNd）。

纯标准库。张量用嵌套 list：[N][C][H][W]。
"""

# ---------------------------------------------------------------- 尺寸公式


def conv2d_output(h_in, padding, dilation, kernel, stride):
    """Conv2d docstring 的 Shape 公式（向下取整）。"""
    return (h_in + 2 * padding - dilation * (kernel - 1) - 1) // stride + 1


def effective_kernel(kernel, dilation):
    """空洞卷积的等效核：dilation*(k-1)+1。"""
    return dilation * (kernel - 1) + 1


def conv_transpose_output(h_in, stride, padding, dilation, kernel, output_padding):
    """转置卷积的 Shape 公式（由 _output_padding 的 min_sizes 反推）。"""
    return (h_in - 1) * stride - 2 * padding + dilation * (kernel - 1) + output_padding + 1


def min_output_for(h_in, stride, padding, dilation, kernel):
    """_output_padding 里的 min_sizes：output_padding = 0 时的尺寸。"""
    return (h_in - 1) * stride - 2 * padding + dilation * (kernel - 1) + 1


def resolve_output_padding(h_in, output_size, stride, padding, dilation, kernel):
    """_output_padding 的 output_size 分支：越界抛 ValueError，否则返回 output_padding。"""
    lo = min_output_for(h_in, stride, padding, dilation, kernel)
    hi = lo + stride - 1
    if output_size < lo or output_size > hi:
        raise ValueError(
            "requested an output size of %d, but valid sizes range from %d to %d" % (output_size, lo, hi)
        )
    return output_size - lo


def same_padding(kernel, stride):
    """padding='same'：源码 Note 明确「不支持 stride 非 1」。"""
    if stride != 1:
        raise ValueError("padding='same' is not supported for strided convolutions")
    return (kernel - 1) // 2


# ---------------------------------------------------------------- 形状与参数量


def weight_shape(in_channels, out_channels, kernel_h, kernel_w, groups=1):
    """_ConvNd 的 weight 形状：(out_channels, in_channels/groups, kH, kW)。"""
    if in_channels % groups != 0:
        raise ValueError("in_channels must be divisible by groups")
    if out_channels % groups != 0:
        raise ValueError("out_channels must be divisible by groups")
    return (out_channels, in_channels // groups, kernel_h, kernel_w)


def param_count(shape, bias=True):
    out, cin_g, kh, kw = shape
    n = out * cin_g * kh * kw
    return n + out if bias else n


def depthwise_separable_params(in_channels, out_channels, kernel, bias=True):
    """DW(groups=in_channels) + PW(1×1) 的参数量。"""
    dw = param_count(weight_shape(in_channels, in_channels, kernel, kernel, in_channels), bias)
    pw = param_count(weight_shape(in_channels, out_channels, 1, 1, 1), bias)
    return dw, pw


# ---------------------------------------------------------------- padding


def pad_index(i, size, mode):
    """返回输入下标 i（可为负/超界）在指定 padding_mode 下的取法。None 表示取 0。"""
    if 0 <= i < size:
        return i
    if mode == "zeros":
        return None
    if mode == "replicate":
        return 0 if i < 0 else size - 1
    if mode == "circular":
        return i % size
    raise ValueError("unsupported padding_mode: %s" % mode)


def sample(x, c, i, j, mode):
    h, w = len(x[c]), len(x[c][0])
    pi = pad_index(i, h, mode)
    pj = pad_index(j, w, mode)
    if pi is None or pj is None:
        return 0.0
    return x[c][pi][pj]


# ---------------------------------------------------------------- 前向


def conv2d(x, weight, bias=None, stride=1, padding=0, dilation=1, groups=1,
           padding_mode="zeros"):
    """互相关（cross-correlation）前向，支持 groups / dilation / stride / padding_mode。"""
    cin = len(x)
    cout = len(weight)
    kh, kw = len(weight[0][0]), len(weight[0][0][0])
    h_in, w_in = len(x[0]), len(x[0][0])
    h_out = conv2d_output(h_in, padding, dilation, kh, stride)
    w_out = conv2d_output(w_in, padding, dilation, kw, stride)
    if h_out <= 0 or w_out <= 0:
        raise ValueError("Calculated output size too small: (%d, %d)" % (h_out, w_out))
    out_per_group = cout // groups
    in_per_group = cin // groups
    out = [[[0.0] * w_out for _ in range(h_out)] for _ in range(cout)]
    for o in range(cout):
        g = o // out_per_group
        for i in range(h_out):
            for j in range(w_out):
                acc = 0.0
                for kc in range(in_per_group):
                    c = g * in_per_group + kc
                    for ki in range(kh):
                        for kj in range(kw):
                            hi = i * stride - padding + ki * dilation
                            wj = j * stride - padding + kj * dilation
                            acc += weight[o][kc][ki][kj] * sample(x, c, hi, wj, padding_mode)
                if bias is not None:
                    acc += bias[o]
                out[o][i][j] = acc
    return out


def support_channels(x, weight, stride=1, padding=0, dilation=1, groups=1,
                     padding_mode="zeros", tol=1e-12):
    """支持集探针：逐个输入通道单独置 1，看哪些输出通道被点亮。"""
    cin = len(x)
    lit = []
    for c in range(cin):
        probe = [[[0.0] * len(x[0][0]) for _ in range(len(x[0]))] for _ in range(cin)]
        probe[c] = [[1.0] * len(x[0][0]) for _ in range(len(x[0]))]
        o = conv2d(probe, weight, None, stride, padding, dilation, groups, padding_mode)
        lit.append([oi for oi, och in enumerate(o) if any(abs(v) > tol for row in och for v in row)])
    return lit
