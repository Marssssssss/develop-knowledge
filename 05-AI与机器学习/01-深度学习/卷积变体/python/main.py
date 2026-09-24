"""卷积变体演示入口：尺寸公式、groups 的连接图、转置卷积的可选尺寸区间。"""

from conv import (
    conv2d,
    conv2d_output,
    conv_transpose_output,
    depthwise_separable_params,
    effective_kernel,
    min_output_for,
    param_count,
    resolve_output_padding,
    weight_shape,
)


def main():
    print("== Conv2d 输出尺寸（H_in=32, k=3）==")
    for pad, dil, st in ((0, 1, 1), (1, 1, 1), (0, 2, 1), (1, 2, 2), (2, 3, 3)):
        print("  pad=%d dil=%d stride=%d → H_out=%d (k_eff=%d)"
              % (pad, dil, st, conv2d_output(32, pad, dil, 3, st), effective_kernel(3, dil)))

    print("\n== 参数量：标准 vs 深度可分离（C_in=3, C_out=16, k=3）==")
    std = param_count(weight_shape(3, 16, 3, 3))
    dw, pw = depthwise_separable_params(3, 16, 3)
    print("  标准 Conv2d   : %d" % std)
    print("  Depthwise     : %d" % dw)
    print("  Pointwise 1×1 : %d" % pw)
    print("  DW+PW         : %d（= 标准的 %.1f%%）" % (dw + pw, 100.0 * (dw + pw) / std))

    print("\n== 空洞卷积等效核 ==")
    for dil in (1, 2, 4, 8):
        print("  dilation=%d → k_eff=%d（感受野 %d×%d）"
              % (dil, effective_kernel(3, dil), effective_kernel(3, dil), effective_kernel(3, dil)))

    print("\n== groups 的连接图（C_in=C_out=4）==")
    for g in (1, 2, 4):
        shape = weight_shape(4, 4, 3, 3, g)
        print("  groups=%d → weight 形状 %s，每组 %d 进 %d 出，参数 %d"
              % (g, shape, 4 // g, 4 // g, param_count(shape)))

    print("\n== 转置卷积：output_size 的合法区间（H_in=4, k=3, stride=2, pad=1）==")
    lo = min_output_for(4, 2, 1, 1, 3)
    print("  合法区间 [%d, %d]（min + stride - 1）" % (lo, lo + 2 - 1))
    for size in range(lo - 1, lo + 3):
        try:
            op = resolve_output_padding(4, size, 2, 1, 1, 3)
            print("  output_size=%d → output_padding=%d，实际输出=%d"
                  % (size, op, conv_transpose_output(4, 2, 1, 1, 3, op)))
        except ValueError as e:
            print("  output_size=%d → 拒绝：%s" % (size, e))

    print("\n== 前向手算对照（1×3×3 配 2×2 核 [[1,2],[3,4]]）==")
    x = [[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]]
    o = conv2d(x, [[[[1.0, 2.0], [3.0, 4.0]]]])
    print("  ", ["%.1f" % v for v in o[0][0]])
    print("  ", ["%.1f" % v for v in o[0][1]])


if __name__ == "__main__":
    main()
