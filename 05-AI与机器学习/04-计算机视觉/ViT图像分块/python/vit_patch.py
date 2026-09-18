# -*- coding: utf-8 -*-
"""ViT 图像分块:patchify、patch embedding 与 Conv2d 的等价性、位置编码、Pre-LN 编码器。

权威口径:A.Dosovitskiy et al.《An Image is Worth 16x16 Words: Transformers for Image
Recognition at Scale》(arXiv:2010.11929)。论文的关键设计与本文件对应:

- §3.1 "we reshape the image into a sequence of flattened 2D patches",
  `N = H·W / P²` —— 224×224、P=16 得 **N = 196**;
- "The Transformer uses constant latent vector size D through all of its layers, so we flatten
  the patches and map to D dimensions with a trainable linear projection";
- "The Transformer's sequence input ... we prepend a learnable embedding to the sequence of
  embedded patches (**[class] token**)";
- "We add **position embeddings** to the patch embeddings to retain positional information.
  We use standard learnable 1D position embeddings ... We did not observe performance gains
  from using more advanced 2D-aware position embeddings";
- 式 (1) `z_0 = [x_class; x_p^1 E; …; x_p^N E] + E_pos`;
  式 (2) `z'_ℓ = MSA(LN(z_{ℓ-1})) + z_{ℓ-1}`;式 (3) `z_ℓ = MLP(LN(z'_ℓ)) + z'_ℓ`
  —— 即 **Pre-LN** 残差结构;式 (4) `y = LN(z_L^0)`(取 [class] token 的输出做分类);
- 论文自认的代价:"Transformers lack some of the **inductive biases** ... such as translation
  equivariance and locality, and therefore do not generalize well when trained on insufficient
  amounts of data"。

本文件不训练任何模型,只实现并验证**分块/嵌入/位置编码/单层编码器**的可验证性质。
"""
import math

PATCH = 16                # 论文标题里的 16x16
IMAGE = 224               # 论文的标准输入分辨率
CHANNELS = 3              # RGB
LATENT = 768              # ViT-Base 的 D
LN_EPS = 1e-5


# ---------------------------------------------------------------- 分块

def patch_grid(h, w, patch, stride=None):
    """返回 (行数, 列数)。stride 默认等于 patch(不重叠分块)。"""
    stride = patch if stride is None else stride
    if h < patch or w < patch:
        return 0, 0
    return (h - patch) // stride + 1, (w - patch) // stride + 1


def n_patches(h, w, patch, stride=None):
    """论文的 N = H·W / P²;非整除时向下取整,边缘像素被丢弃。"""
    rows, cols = patch_grid(h, w, patch, stride)
    return rows * cols


def patchify(img, patch, stride=None):
    """img 为 HWC 嵌套列表,返回按行优先展平的 patch 向量列表。

    每个 patch 的展平顺序是 (patch_y, patch_x, channel) —— 对应论文"flattened 2D patches"。
    """
    stride = patch if stride is None else stride
    rows, cols = patch_grid(len(img), len(img[0]), patch, stride)
    out = []
    for ry in range(rows):
        for rx in range(cols):
            vec = []
            for dy in range(patch):
                for dx in range(patch):
                    vec.extend(img[ry * stride + dy][rx * stride + dx])
            out.append(vec)
    return out


def unpatchify(patches, h, w, patch, stride=None, channels=CHANNELS):
    """patchify 的逆(构造用,便于验证搬运无损)。"""
    stride = patch if stride is None else stride
    rows, cols = patch_grid(h, w, patch, stride)
    img = [[[0.0] * channels for _ in range(w)] for _ in range(h)]
    for i, vec in enumerate(patches):
        ry, rx = divmod(i, cols)
        k = 0
        for dy in range(patch):
            for dx in range(patch):
                for c in range(channels):
                    img[ry * stride + dy][rx * stride + dx][c] = vec[k]
                    k += 1
    return img


def patch_dim(patch, channels=CHANNELS):
    """一个 patch 展平后的长度 P²·C。"""
    return patch * patch * channels


def shift_image(img, dx, dy=0, fill=0.0):
    """整体平移图像(越界填 fill),用于检验分块的平移行为。"""
    h, w = len(img), len(img[0])
    out = [[[fill] * len(img[0][0]) for _ in range(w)] for _ in range(h)]
    for y in range(h):
        for x in range(w):
            sy, sx = y - dy, x - dx
            if 0 <= sy < h and 0 <= sx < w:
                out[y][x] = list(img[sy][sx])
    return out


def patch_embed_params(patch, channels, dim):
    """patch embedding 的参数量 = P²·C·D(线性投影,无 bias)。"""
    return patch_dim(patch, channels) * dim


def pos_embed_params(n_tokens, dim):
    """1D 可学习位置编码的参数量 = (N + 1)·D(含 [class] token 的那一份)。"""
    return n_tokens * dim


def tokens_total(n, with_class=True):
    return n + 1 if with_class else n


# ---------------------------------------------------------------- 线性 / 卷积等价

def lcg_vector(n, seed, lo=-1.0, hi=1.0):
    """确定性伪随机向量(避免依赖 random 模块,保证跨语言复现)。"""
    state = seed
    out = []
    for _ in range(n):
        state = (1103515245 * state + 12345) % (2 ** 31)
        out.append(lo + (hi - lo) * (state / float(2 ** 31)))
    return out


def lcg_matrix(rows, cols, seed, lo=-1.0, hi=1.0):
    flat = lcg_vector(rows * cols, seed, lo, hi)
    return [flat[i * cols:(i + 1) * cols] for i in range(rows)]


def linear(vec, weight):
    """weight 为 [out_dim][in_dim]。"""
    return [sum(w * v for w, v in zip(row, vec)) for row in weight]


def conv2d_at(img, weight, ry, rx, patch, stride):
    """在 (ry, rx) 处对 HWC 图像做一次卷积输出;weight 为 [out_dim][c][kh][kw]。

    这是"patch embedding 等价于 stride = patch 的 Conv2d"这条论文结论的卷积侧实现。
    """
    ch = len(img[0][0])
    res = [0.0] * len(weight)
    for o, w in enumerate(weight):
        s = 0.0
        for dy in range(patch):
            for dx in range(patch):
                px = img[ry * stride + dy][rx * stride + dx]
                for c in range(ch):
                    s += w[c][dy][dx] * px[c]
        res[o] = s
    return res


def linear_weight_to_conv(weight, patch, channels):
    """把 [out_dim][P²·C] 的线性权重改排成 [out_dim][C][P][P] 的卷积核。

    两者只是内存排布不同:线性层那一维的索引是 (dy·P + dx)·C + c。
    """
    out = []
    for row in weight:
        kernel = [[[0.0] * patch for _ in range(patch)] for _ in range(channels)]
        for dy in range(patch):
            for dx in range(patch):
                for c in range(channels):
                    kernel[c][dy][dx] = row[(dy * patch + dx) * channels + c]
        out.append(kernel)
    return out


# ---------------------------------------------------------------- 位置编码

def add_pos(tokens, pos):
    return [[a + b for a, b in zip(t, p)] for t, p in zip(tokens, pos)]


def neighbour_pairs(grid_rows, grid_cols):
    """行优先展平后,1D 相邻下标同时是**空间水平相邻**的对数。"""
    total = grid_rows * grid_cols - 1
    horizontal = grid_rows * (grid_cols - 1)
    return horizontal, total


def vertical_index_gap(grid_cols):
    """行优先展平后,空间垂直相邻的两个 token 在 1D 序列里恰好相距 W/P。"""
    return grid_cols


# ---------------------------------------------------------------- 编码器(Pre-LN)

def layernorm(x, gamma=None, beta=None, eps=LN_EPS):
    n = len(x)
    mean = sum(x) / n
    var = sum((v - mean) ** 2 for v in x) / n
    inv = 1.0 / math.sqrt(var + eps)
    out = [(v - mean) * inv for v in x]
    if gamma is not None:
        out = [o * g + b for o, g, b in zip(out, gamma, beta)]
    return out


def gelu(x):
    """GELU 的 tanh 近似(Hendrycks & Gimpel 原式;ViT 的 MLP 用 GELU)。"""
    return 0.5 * x * (1.0 + math.tanh(math.sqrt(2.0 / math.pi) * (x + 0.044715 * x ** 3)))


def softmax_row(v):
    m = max(v)
    ex = [math.exp(x - m) for x in v]
    s = sum(ex)
    return [x / s for x in ex]


def attention(tokens, wq, wk, wv, d):
    """单头自注意力:softmax(QKᵀ/√d)·V。"""
    q = [linear(t, wq) for t in tokens]
    k = [linear(t, wk) for t in tokens]
    v = [linear(t, wv) for t in tokens]
    scale = math.sqrt(float(d))
    out = []
    for i in range(len(tokens)):
        scores = [sum(a * b for a, b in zip(q[i], k[j])) / scale for j in range(len(tokens))]
        probs = softmax_row(scores)
        out.append([sum(probs[j] * v[j][c] for j in range(len(tokens))) for c in range(len(v[0]))])
    return out


def mlp_block(x, w1, b1, w2, b2):
    """两层 MLP + GELU(论文里的 MLP 子层)。"""
    hidden = [gelu(a + b) for a, b in zip(linear(x, w1), b1)]
    return [a + b for a, b in zip(linear(hidden, w2), b2)]


def transformer_block(tokens, params):
    """论文式 (2)(3):Pre-LN 残差块 z' = MSA(LN(z)) + z;z = MLP(LN(z')) + z'。"""
    wq, wk, wv, w1, b1, w2, b2, g1, be1, g2, be2 = params
    d = len(tokens[0])
    nz = [layernorm(t, g1, be1) for t in tokens]
    att = attention(nz, wq, wk, wv, d)
    z1 = [[a + b for a, b in zip(t, h)] for t, h in zip(tokens, att)]
    nz2 = [layernorm(t, g2, be2) for t in z1]
    mlp_out = [mlp_block(t, w1, b1, w2, b2) for t in nz2]
    return [[a + b for a, b in zip(t, m)] for t, m in zip(z1, mlp_out)]


def random_block_params(d, hidden, seed=7):
    """生成一套确定性参数,形状与 ViT 的一个编码器层一致。"""
    return (lcg_matrix(d, d, seed, -0.5, 0.5),
            lcg_matrix(d, d, seed + 1, -0.5, 0.5),
            lcg_matrix(d, d, seed + 2, -0.5, 0.5),
            lcg_matrix(hidden, d, seed + 3, -0.5, 0.5),
            lcg_vector(hidden, seed + 4, -0.1, 0.1),
            lcg_matrix(d, hidden, seed + 5, -0.5, 0.5),
            lcg_vector(d, seed + 6, -0.1, 0.1),
            lcg_vector(d, seed + 7, 0.9, 1.1),
            lcg_vector(d, seed + 8, -0.1, 0.1),
            lcg_vector(d, seed + 9, 0.9, 1.1),
            lcg_vector(d, seed + 10, -0.1, 0.1))


# ---------------------------------------------------------------- 参数与算力

def encoder_params(d, layers, mlp_ratio=4.0):
    """一层 = MSA(4D²+4D) + MLP(2·4D²+5D) + 两个 LN(各 2D)。"""
    attn = 4 * d * d + 4 * d
    hidden = int(d * mlp_ratio)
    mlp = 2 * d * hidden + hidden + d
    ln = 2 * (2 * d)
    return layers * (attn + mlp + ln)


def vit_base_params(patch=PATCH, channels=CHANNELS, dim=LATENT, layers=12, classes=1000):
    """ViT-Base(12 层、D=768、MLP 隐层 3072)的总参数量估算。

    论文 Table 1 给出的 ViT-Base 是 **86M**;这里按"patch 嵌入(无 bias) + 位置编码 +
    12 层编码器 + 末尾 LN + 单层分类头(有 bias)"逐项加,用来核对那个 86M 是怎么来的。
    """
    n = n_patches(IMAGE, IMAGE, patch)
    total = patch_embed_params(patch, channels, dim)
    total += pos_embed_params(tokens_total(n), dim)
    total += encoder_params(dim, layers)
    total += 2 * dim
    total += dim * classes + classes
    return total


def attention_ops(n_tokens, d):
    """单个头一层注意力的乘加量(只算 QKᵀ 的 N²D 这一项)。"""
    return n_tokens * n_tokens * d


def cnn_layers_for_global_receptive_field(size, kernel=3):
    """3×3 卷积堆到覆盖整幅图需要多少层:stride=1 时感受野 = 1 + L·(k-1)。"""
    span = kernel - 1
    l = 0
    rf = 1
    while rf < size:
        rf += span
        l += 1
    return l
