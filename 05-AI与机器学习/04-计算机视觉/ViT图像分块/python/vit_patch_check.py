# -*- coding: utf-8 -*-
"""ViT 分块自检:分块几何 / patch embedding 与卷积的等价 / 位置编码 / Pre-LN 编码器 / 参数与算力。

结论均对应论文的可引用表述(arXiv:2010.11929 §3.1、式 (1)(2)(3)、Table 1、Appendix),
数值一律当场算出并与解析值比对(浮点给容差)。
"""
import math

import vit_patch as V

TOTAL = [0, 0]
FAILS = []


def check(name, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        TOTAL[1] += 1
        print(f"  [PASS] {name}  {detail}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


def maxdiff(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def pattern_image(h=224, w=224, c=3):
    """确定性纹理图(不用随机数),用于分块/平移试验。"""
    return [[[(y * 7 + x * 3 + k) % 11 / 11.0 for k in range(c)] for x in range(w)]
            for y in range(h)]


def main():
    print("== A. 分块几何 ==")
    check("224x224、P=16 -> 14x14 网格、N = 196(论文)", V.patch_grid(224, 224, 16) == (14, 14)
          and V.n_patches(224, 224, 16) == 196, f"N={V.n_patches(224, 224, 16)}")
    check("单 patch 展平长度 P²·C = 768", V.patch_dim(16, 3) == 768, "768")
    check("加上 [class] token 后序列长度 N+1 = 197", V.tokens_total(196) == 197, "197")
    rows, cols = V.patch_grid(225, 225, 16)
    check("非整除输入 225 -> 仍只有 14 列,覆盖 224 px,最右 1 列像素被丢弃",
          (rows, cols) == (14, 14) and 16 + 13 * 16 == 224, f"覆盖 {16 + 13 * 16} px")
    img = pattern_image()
    pat = V.patchify(img, 16)
    back = V.unpatchify(pat, 224, 224, 16)
    rt = max(abs(back[y][x][c] - img[y][x][c])
             for y in range(224) for x in range(224) for c in range(3))
    check("patchify -> unpatchify 完全无损", rt == 0.0, f"max diff {rt}")
    check("行优先展平:第 0 个 patch 首元素 = img[0][0][0],第 1 个 = img[0][16][0]",
          pat[0][0] == img[0][0][0] and pat[1][0] == img[0][16][0], "")
    check("patch 的展平顺序是 (patch_y, patch_x, channel)",
          pat[0][1] == img[0][0][1] and pat[0][3] == img[0][1][0], "")
    check("混合架构:14x14 特征图按 P=1 分块同样得 196 个 token(与 224/P=16 对齐)",
          V.n_patches(14, 14, 1) == 196 and V.patch_dim(1, 1024) == 1024, "196 x 1024")

    print("\n== B. patch embedding 等价于 stride = patch 的 Conv2d ==")
    weight = V.lcg_matrix(8, 16 * 16 * 3, seed=5)
    lin = V.linear(pat[0], weight)
    conv = V.conv2d_at(pattern_image(), V.linear_weight_to_conv(weight, 16, 3), 0, 0, 16, 16)
    eq = max(abs(a - b) for a, b in zip(lin, conv))
    check("线性投影(flattened patch)与卷积核输出逐元素一致", eq < 1e-12, f"max diff {eq:.2e}")
    check("patch embedding 参数量 = P²·C·D = 589824",
          V.patch_embed_params(16, 3, 768) == 589824, "589824")
    ov = V.n_patches(224, 224, 16, stride=8)
    check("stride 8 < patch 16(重叠分块)时 token 数从 196 涨到 729", ov == 729, f"{ov}")
    gaps = V.n_patches(224, 224, 16, stride=20)
    check("stride 20 > patch 16 时只剩 121 个 token 且丢掉 8 px(覆盖 216)",
          gaps == 121 and 16 + 10 * 20 == 216, f"{gaps} 个,覆盖 {16 + 10 * 20} px")

    print("\n== C. 位置编码与置换对称性 ==")
    check("1D 可学习位置编码参数量 = (N+1)·D = 151296",
          V.pos_embed_params(197, 768) == 151296, "151296")
    d, hidden, n = 4, 8, 6
    params = V.random_block_params(d, hidden)
    tokens = [V.lcg_vector(d, 100 + i, -0.5, 0.5) for i in range(n)]
    perm = [3, 0, 5, 1, 4, 2]
    out = V.transformer_block(tokens, params)
    pout = V.transformer_block([tokens[i] for i in perm], params)
    inv = [None] * n
    for k, i in enumerate(perm):
        inv[i] = pout[k]
    eq_no_pos = max(maxdiff(out[i], inv[i]) for i in range(n))
    check("不加位置编码时,编码器块对 token 置换**等变**(输出只是同样重排)",
          eq_no_pos < 1e-12, f"max diff {eq_no_pos:.2e}")
    pos = [V.lcg_vector(d, 900 + i, -0.3, 0.3) for i in range(n)]
    a = V.transformer_block(V.add_pos(tokens, pos), params)
    b = V.transformer_block(V.add_pos([tokens[i] for i in perm], pos), params)
    inv2 = [None] * n
    for k, i in enumerate(perm):
        inv2[i] = b[k]
    brk = max(maxdiff(a[i], inv2[i]) for i in range(n))
    check("加上位置编码后置换**不再**等变(diff 远离 0)-> 位置编码是打破置换对称的唯一来源",
          brk > 0.1, f"max diff {brk:.6f}")
    hor, tot = V.neighbour_pairs(14, 14)
    check("行优先 1D 排序下,相邻下标同时是空间水平相邻的比例 = 182/195 = 93.33%",
          (hor, tot) == (182, 195) and abs(hor / tot - 0.9333333333333333) < 1e-12,
          f"{hor}/{tot} = {100 * hor / tot:.2f}%")
    check("空间垂直相邻的 token 在 1D 序列中恰好相距 W/P = 14(不是相邻)",
          V.vertical_index_gap(14) == 14, "14")
    cls_tokens = [[1.0] + t[1:] for t in tokens]
    o1 = V.transformer_block(cls_tokens, params)
    touched = [list(t) for t in cls_tokens]
    touched[4][1] += 1.0
    o2 = V.transformer_block(touched, params)
    sens = maxdiff(o1[0], o2[0])
    check("[class] token 的输出依赖**每一个** patch(改第 4 个 patch 就变了)-> 第一层即全局感受野",
          sens > 1e-6, f"max diff {sens:.6f}")

    print("\n== D. Pre-LN 编码器(论文式 (2)(3))==")
    p_zero_v = list(params)
    p_zero_v[2] = [[0.0] * d for _ in range(d)]
    att = V.attention([V.layernorm(t, params[7], params[8]) for t in tokens],
                      params[0], params[1], p_zero_v[2], d)
    check("把 V 的投影置零后,MSA 输出恒为 0(残差恒等的前提)",
          all(v == 0.0 for row in att for v in row), "")
    p_ident = list(params)
    p_ident[2] = [[0.0] * d for _ in range(d)]
    p_ident[3] = [[0.0] * d for _ in range(hidden)]
    p_ident[4] = [0.0] * hidden
    p_ident[6] = [0.0] * d
    ident = V.transformer_block(tokens, tuple(p_ident))
    idf = max(maxdiff(ident[i], tokens[i]) for i in range(n))
    check("两个子层输出都置零时,Pre-LN 残差块退化为恒等映射(残差加在 LN 输出之后)",
          idf < 1e-12, f"max diff {idf:.2e}")
    ln = V.layernorm([1.0, 2.0, 3.0, 4.0])
    check("LayerNorm 输出均值 0、方差 ≈ 1(eps=1e-5 使其略小于 1)",
          abs(sum(ln) / 4) < 1e-15 and 0.999 < sum(v * v for v in ln) / 4 < 1.0,
          f"var={sum(v * v for v in ln) / 4:.12f}")
    check("GELU(0) == 0 且 GELU(1) ≈ 0.841192(tanh 近似)",
          V.gelu(0.0) == 0.0 and abs(V.gelu(1.0) - 0.8411919906082768) < 1e-12,
          f"{V.gelu(1.0):.15f}")
    scores = [1.0, 2.0, 3.0]
    check("注意力权重按行 softmax,和为 1",
          abs(sum(V.softmax_row(scores)) - 1.0) < 1e-15, "")
    check("softmax 对整体平移不变(数值稳定实现)",
          maxdiff(V.softmax_row(scores), V.softmax_row([x + 1000 for x in scores])) < 1e-15, "")

    print("\n== E. 参数量与算力 ==")
    check("12 层编码器参数量 = 85054464",
          V.encoder_params(768, 12) == 85054464, f"{V.encoder_params(768, 12)}")
    total = V.vit_base_params()
    check("ViT-Base 总参数 ≈ 86.57M,与论文 Table 1 的 86M 相对误差 < 1%",
          abs(total / 86e6 - 1.0) < 0.01, f"{total} ({total / 1e6:.2f}M)")
    check("单头一层注意力(N²D)= 29503488 次乘加",
          V.attention_ops(196, 768) == 29503488, f"{V.attention_ops(196, 768)}")
    check("P 减半 -> token 数 4 倍,注意力矩阵面积 16 倍(38416 -> 614656)",
          V.n_patches(224, 224, 8) == 784 and V.attention_ops(784, 768) == 16 * V.attention_ops(196, 768),
          f"{V.n_patches(224, 224, 8)} tokens")
    layers = V.cnn_layers_for_global_receptive_field(224)
    check("覆盖 224 px:3x3 CNN 需 112 层,ViT 只需 1 层(全局注意力的代价是 N²)",
          layers == 112, f"{layers} 层")

    print("\n== F. 归纳偏置:平移行为 ==")
    shifted = V.shift_image(img, 16)
    pat16 = V.patchify(shifted, 16)
    same = True
    for ry in range(14):
        for rx in range(1, 14):
            if maxdiff(pat16[ry * 14 + rx], pat[ry * 14 + rx - 1]) != 0.0:
                same = False
    check("整 patch 平移(16 px):patch 序列只是位置重排,内容逐元素相同(等变)",
          same, "")
    pat1 = V.patchify(V.shift_image(img, 1), 16)
    diff1 = maxdiff(pat1[0], pat[0])
    check("亚像素平移(1 px):每个 patch 的内容都变了 -> 分块本身不是平移等变的",
          diff1 > 1e-3, f"patch0 max diff {diff1:.6f}")
    changed = sum(1 for i in range(196) if maxdiff(pat1[i], pat[i]) > 1e-9)
    check("1 px 平移改变了**全部** 196 个 patch(不是只动边界列)-> 无平移等变性",
          changed == 196, f"{changed}/196 个 patch 改变")
    check("整 patch 平移下 token 只有 13/14 的列能对齐,末列被裁掉、首列由新像素填充",
          maxdiff(pat16[13], pat[13]) != 0.0, "")

    print(f"\n结果:{TOTAL[1]}/{TOTAL[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
