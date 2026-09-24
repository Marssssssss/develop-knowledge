"""卷积变体自检：尺寸公式 + groups 支持集探针 + 转置卷积的 output_padding 区间。"""

from conv import (
    conv2d,
    conv2d_output,
    conv_transpose_output,
    depthwise_separable_params,
    effective_kernel,
    min_output_for,
    param_count,
    resolve_output_padding,
    same_padding,
    support_channels,
    weight_shape,
)

TOL = 1e-9
FAILED = []
PASSED = 0


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
        print("  ok  %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def close(name, a, b, tol=TOL):
    ok("%s (%r ≈ %r)" % (name, a, b), abs(a - b) <= tol * max(1.0, abs(a), abs(b)))


def allclose(name, xs, ys, tol=TOL):
    ok("%s (逐项 %d 个)" % (name, len(xs)),
       len(xs) == len(ys) and all(abs(a - b) <= tol * max(1.0, abs(a), abs(b)) for a, b in zip(xs, ys)))


def eye_weight(cout, cin_g, kh, kw, seed=1):
    w = []
    s = seed
    for o in range(cout):
        chans = []
        for c in range(cin_g):
            rows = []
            for i in range(kh):
                row = []
                for j in range(kw):
                    s = (1103515245 * s + 12345) & 0x7FFFFFFF
                    row.append((s / 0x7FFFFFFF) * 2 - 1)
                rows.append(row)
            chans.append(rows)
        w.append(chans)
    return w


def main():
    # ---- 1. 输出尺寸公式 ----
    ok("H=32,k=3,pad=0,stride=1 → 30", conv2d_output(32, 0, 1, 3, 1) == 30)
    ok("H=32,k=3,pad=1,stride=1 → 32", conv2d_output(32, 1, 1, 3, 1) == 32)
    ok("H=7,k=3,stride=2 → 3（floor((7-2-1)/2)+1）", conv2d_output(7, 0, 1, 3, 2) == 3)
    ok("H=7,k=3,stride=2,dilation=2 → 2", conv2d_output(7, 0, 2, 3, 2) == 2)
    ok("等效核 k_eff = d(k-1)+1", effective_kernel(3, 2) == 5 and effective_kernel(3, 1) == 3)
    ok("公式 ≡ 用 k_eff 当普通核（32,k_eff=5 → 28）", conv2d_output(32, 0, 2, 3, 1) == 32 - effective_kernel(3, 2) + 1)
    ok("非整除时向下取整：H=8,k=3,stride=2 → 3（(8-2-1)/2=2.5）", conv2d_output(8, 0, 1, 3, 2) == 3)
    ok("输出非正：H=2,k=5 → -2（源码抛 'too small'）", conv2d_output(2, 0, 1, 5, 1) == -2)

    try:
        same_padding(3, 2)
        ok("padding='same' + stride=2 应当报错", False)
    except ValueError as e:
        ok("padding='same' 不支持 stride≠1：%s" % e, "stride" in str(e))
    ok("padding='same', k=3 → pad=1", same_padding(3, 1) == 1)
    ok("padding='same', k=5 → pad=2", same_padding(5, 1) == 2)
    ok("same + stride=1 时输出 = 输入", conv2d_output(16, same_padding(3, 1), 1, 3, 1) == 16)

    # ---- 2. 形状与参数量 ----
    ok("weight 形状 (out, in/groups, kH, kW)", weight_shape(6, 12, 3, 3, 3) == (12, 2, 3, 3))
    ok("groups=1 时 (out, in, k, k)", weight_shape(3, 16, 3, 3) == (16, 3, 3, 3))
    try:
        weight_shape(5, 16, 3, 3, 2)
        ok("in_channels 不被 groups 整除应当报错", False)
    except ValueError as e:
        ok("in_channels % groups != 0 报错", "in_channels" in str(e))
    try:
        weight_shape(6, 5, 3, 3, 2)
        ok("out_channels 不被 groups 整除应当报错", False)
    except ValueError as e:
        ok("out_channels % groups != 0 报错", "out_channels" in str(e))
    ok("参数量 = out·(in/g)·kH·kW + out(bias)", param_count((16, 3, 3, 3)) == 16 * 3 * 9 + 16)
    ok("bias=False 时不含 bias 项", param_count((16, 3, 3, 3), bias=False) == 16 * 3 * 9)
    dw, pw = depthwise_separable_params(3, 16, 3)
    ok("DW 参数 = in·1·k·k + in = 3·9+3 = 30", dw == 30)
    ok("PW 参数 = out·in·1·1 + out = 16·3+16 = 64", pw == 64)
    ok("DW+PW = 94 < 标准卷积 448", dw + pw == 94 and dw + pw < param_count((16, 3, 3, 3)))

    # ---- 3. 前向：与手算一致 ----
    x = [[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]]          # 1×3×3
    w = [[[[1.0, 2.0], [3.0, 4.0]]]]                                    # 1×1×2×2
    o = conv2d(x, w)
    ok("1×3×3 配 2×2 → 2×2", len(o) == 1 and len(o[0]) == 2 and len(o[0][0]) == 2)
    close("手算 [0][0] = 1·1+2·2+3·4+4·5 = 37", o[0][0][0], 37.0)
    close("手算 [0][1] = 1·2+2·3+3·5+4·6 = 47", o[0][0][1], 47.0)
    close("手算 [1][0] = 1·4+2·5+3·7+4·8 = 67", o[0][1][0], 67.0)
    close("手算 [1][1] = 1·5+2·6+3·8+4·9 = 77", o[0][1][1], 77.0)
    o_b = conv2d(x, w, bias=[10.0])
    allclose("bias 加到每个输出点", o_b[0][0], [47.0, 57.0])

    # stride / padding
    o_s = conv2d(x, w, stride=2)
    ok("3×3, k=2, stride=2 → 1×1", len(o_s[0]) == 1 and len(o_s[0][0]) == 1)
    allclose("stride=2 只取左上角", o_s[0][0], [37.0])
    o_p = conv2d(x, w, padding=1)
    ok("3×3, k=2, pad=1 → 4×4", len(o_p[0]) == 4 and len(o_p[0][0]) == 4)

    # ---- 4. padding_mode ----
    o_z = conv2d(x, w, padding=1, padding_mode="zeros")
    o_r = conv2d(x, w, padding=1, padding_mode="replicate")
    o_c = conv2d(x, w, padding=1, padding_mode="circular")
    ok("三种 padding_mode 在角落处结果不同", o_z[0][0][0] != o_r[0][0][0] and o_z[0][0][0] != o_c[0][0][0])
    close("zeros：角落 4·x[0][0] = 4", o_z[0][0][0], 4.0)
    close("replicate：角落各项都取 x[0][0]=1 → 1+2+3+4 = 10", o_r[0][0][0], 10.0)
    close("circular：角落绕回 → 1·9+2·7+3·3+4·1 = 36", o_c[0][0][0], 36.0)
    try:
        conv2d(x, w, padding=1, padding_mode="reflect")
        ok("未知 padding_mode 应当报错", False)
    except ValueError as e:
        ok("未知 padding_mode 报 ValueError（本实现只建模三种）", "padding_mode" in str(e))

    # ---- 5. 空洞卷积 ----
    x4 = [[[float(i * 4 + j + 1) for j in range(4)] for i in range(4)]]      # 1×4×4
    w2 = [[[[1.0, 1.0], [1.0, 1.0]]]]
    o_d1 = conv2d(x4, w2)
    o_d2 = conv2d(x4, w2, dilation=2)
    ok("4×4, k=2, dil=1 → 3×3", len(o_d1[0]) == 3 and len(o_d1[0][0]) == 3)
    ok("4×4, k=2, dil=2 → 2×2（k_eff=3）", len(o_d2[0]) == 2 and len(o_d2[0][0]) == 2)
    close("dil=2 左上角 = x[0][0]+x[0][2]+x[2][0]+x[2][2] = 1+3+9+11", o_d2[0][0][0], 24.0)
    close("dil=1 左上角 = 1+2+5+6", o_d1[0][0][0], 14.0)
    ok("空洞不增加参数量", param_count((1, 1, 2, 2)) == param_count((1, 1, 2, 2)))

    # ---- 6. groups 与 depthwise ----
    xg = [[[1.0, 2.0], [3.0, 4.0]] for _ in range(4)]                          # [C=4][2][2]
    wg = eye_weight(4, 2, 2, 2, seed=3)                                      # (4, 2, 2, 2), groups=2
    lit = support_channels(xg, wg, groups=2)
    ok("groups=2：in0/in1 只点亮 out0/out1", lit[0] == [0, 1] and lit[1] == [0, 1])
    ok("groups=2：in2/in3 只点亮 out2/out3", lit[2] == [2, 3] and lit[3] == [2, 3])
    wd = eye_weight(4, 1, 3, 3, seed=5)                                      # depthwise: cin/g = 1
    xd = [[[1.0] * 5 for _ in range(5)] for _ in range(4)]                    # [C=4][5][5]
    lit_d = support_channels(xd, wd, padding=1, groups=4)
    ok("depthwise(groups=in=4)：每个输入通道只点亮对应的 1 个输出通道",
       all(len(v) == 1 and v[0] == c for c, v in enumerate(lit_d)))
    # depthwise multiplier K=2：cout=8, groups=4 → out[2j], out[2j+1] 都只看 in[j]
    wm = eye_weight(8, 1, 3, 3, seed=7)
    lit_m = support_channels(xd, wm, padding=1, groups=4)
    ok("K=2：in_j → out[2j], out[2j+1]", lit_m[0] == [0, 1] and lit_m[3] == [6, 7])
    # groups=1 时每个输入通道点亮所有输出通道（负控）
    w1 = eye_weight(2, 4, 3, 3, seed=9)
    lit_1 = support_channels(xd, w1, padding=1, groups=1)
    ok("groups=1：每个输入通道点亮全部 2 个输出通道（负控）", all(v == [0, 1] for v in lit_1))

    # ---- 7. 转置卷积 ----
    ok("h=2,s=2,p=0,k=3,op=0 → 5", conv_transpose_output(2, 2, 0, 1, 3, 0) == 5)
    ok("h=4,s=2,p=1,k=3,op=1 → 8（2× 上采样）", conv_transpose_output(4, 2, 1, 1, 3, 1) == 8)
    ok("s=1,p=0,k=3 → h+2", conv_transpose_output(6, 1, 0, 1, 3, 0) == 8)
    ok("min_output 等于 op=0 的尺寸", min_output_for(2, 2, 0, 1, 3) == conv_transpose_output(2, 2, 0, 1, 3, 0))
    ok("output_padding 步长上界：s=2 → 只能多 0/1", resolve_output_padding(2, 5, 2, 0, 1, 3) == 0
       and resolve_output_padding(2, 6, 2, 0, 1, 3) == 1)
    try:
        resolve_output_padding(2, 7, 2, 0, 1, 3)
        ok("output_size 超出 [min, min+s-1] 应当报错", False)
    except ValueError as e:
        ok("output_size 越界报 ValueError：%s" % e, "valid sizes range" in str(e))
    try:
        resolve_output_padding(2, 4, 2, 0, 1, 3)
        ok("output_size 小于 min 应当报错", False)
    except ValueError as e:
        ok("output_size 过小也报错", "valid sizes range" in str(e))
    ok("s=1 时区间退化为单点（无法用 output_padding 调尺寸）",
       resolve_output_padding(2, min_output_for(2, 1, 0, 1, 3), 1, 0, 1, 3) == 0)
    ok("dil=2,k=3 的转置：h=3,s=1 → 7", conv_transpose_output(3, 1, 0, 2, 3, 0) == 7)

    print()
    print("断言总数 %d，失败 %d" % (PASSED + len(FAILED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  失败：%s" % f)
        raise SystemExit(1)
    print("卷积变体自检全部通过")


if __name__ == "__main__":
    main()
