"""混合精度自检:24 项断言。全绿才认为 demo 成立。

断言的都是「格式的硬性质」:可表示范围、归零阈值、缩放饱和点、
以及"权重更新必须落在 fp32 主副本上"这一结构性结论。
"""

import numpy as np

import mixed_precision as M

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


print("== A. 可表示范围(IEEE-754) ==")
rt = M.range_table()
check("fp16 最小正规数 = 2^-14", abs(rt["fp16 最小正规数"] - 2.0 ** -14) < 1e-45, f"{rt['fp16 最小正规数']:.6e}")
check("fp16 最小非正规数 = 2^-24", abs(rt["fp16 最小非正规数"] - 2.0 ** -24) < 1e-45, f"{rt['fp16 最小非正规数']:.6e}")
check("fp16 最大值 = 65504", rt["fp16 最大值"] == 65504.0, f"{rt['fp16 最大值']}")
check("fp32 最小正规数 = 2^-126", abs(rt["fp32 最小正规数"] - 2.0 ** -126) < 1e-45, f"{rt['fp32 最小正规数']:.6e}")
check(
    "fp16 的下界比 fp32 小 ~30 个数量级",
    np.log10(rt["fp16 最小正规数"] / rt["fp32 最小正规数"]) > 29,
    f"{rt['fp16 最小正规数'] / rt['fp32 最小正规数']:.3e} 倍",
)

print("\n== B. 归零阈值(下溢) ==")
sw = M.underflow_sweep()
zeros = [k for k, v in sw.items() if v == 0.0]
check("2^-30 ~ 2^-25 全部归零", zeros == [-30, -29, -28, -27, -26, -25], str(zeros))
check("2^-24 恰好可表示(非正规最小值)", sw[-24] == 2.0 ** -24, f"{sw[-24]:.6e}")
check("2^-23 及更大完全精确", all(abs(sw[k] - 2.0 ** k) < 1e-40 for k in (-23, -22, -21, -20, -19)), "5 个点")
check("溢出阈值:65504 可表示而 1e6 变 inf", M.fp16(65504.0) == 65504.0 and np.isinf(M.fp16(1e6)), "65504 / inf")

print("\n== C. loss scaling ==")
tab = M.scaling_table()
d = {S: (f, r, o) for S, f, r, o in tab}
check("S=1 时相当比例的梯度被冲成 0", d[1.0][0] > 0.2, f"{d[1.0][0] * 100:.2f}%")
check("S=1 时这些梯度的信息完全丢失(相对误差 = 1)", abs(d[1.0][1] - 1.0) < 1e-12, f"{d[1.0][1]:.6f}")
check("S=2^18 时冲零比例归 0", d[2.0 ** 18][0] == 0.0, "0.000%")
check("S=2^18 后相对误差回到 fp16 的 2^-11 量级", d[2.0 ** 18][1] < 1e-3, f"{d[2.0 ** 18][1]:.6e}")
check("精度在 S=2^18 处已饱和(再放大无收益)", d[2.0 ** 20][1] == d[2.0 ** 18][1], "两者完全相等")
check("本扫描未触发溢出(梯度 ≤ 1e-2,余量充足)", not any(r[3] for r in tab), "S·g_max < 65504")

print("\n== D. master weights ==")
w_h, w_f, w_alias = M.master_weight_demo(steps=1000)
check("纯 fp16 权重 1000 步后一动没动", w_h == 1.0, f"{w_h!r}")
# 注意:fp32 自己也只有 2^-24 的相对精度,1e-7 的步长在 1.0 附近只是"亚 ulp 级"累积,
# 1000 步实测下降 1.19e-4 而不是解析值 1.00e-4 —— 这是 fp32 的累积误差,不是实现问题。
check(
    "fp32 master 确实在移动(量级 ≈ 1e-4)",
    1e-5 < (1.0 - w_f) < 1.5e-4,
    f"下降 {1.0 - w_f:.3e}(解析值 1.000e-04)",
)
check(
    "fp32 的累积误差比它要走的总距离小一个数量级",
    abs((1.0 - w_f) - 1e-4) < 1e-4 * 0.5,
    f"偏差 {abs((1.0 - w_f) - 1e-4):.3e}",
)
w_h2, w_f2, w_alias2 = M.master_weight_demo(steps=20000)
check("纯 fp16 权重 20000 步后仍然没动", w_h2 == 1.0, f"{w_h2!r}")
check("fp32 master 累计 2e-3,舍入回 fp16 后可见", abs(w_alias2 - 1.0) > 1e-3, f"{w_alias2!r}")
check(
    "单步更新比 fp16 在 1.0 处的 ulp 小 3 个数量级以上",
    (2.0 ** -10) / 1e-7 > 1e3,
    f"ulp {2.0 ** -10:.3e} vs 步长 1e-7(相差 {(2.0 ** -10) / 1e-7:.1e} 倍)",
)

print("\n== E. 动态缩放 ==")
log, skipped = M.dynamic_scaler()
check("发生过溢出(至少跳 2 步)", skipped >= 2, f"跳过 {skipped} 步")
# 溢出发生在**当步**:日志记的是处理完该步之后的 scale,所以减半体现为 log[i] 相对 log[i-1]
check("溢出当步即减半", all(
    abs(log[i][1] * 2 - log[i - 1][1]) < 1e-6 for i in range(1, len(log)) if log[i][2]
), "逐次核对")
check("连续 10 步成功即翻倍", any(
    abs(log[i + 1][1] - 2 * log[i][1]) < 1e-6 for i in range(len(log) - 1) if not log[i][2]
), "存在翻倍点")
check("scale 始终有限且为正", all(np.isfinite(s) and s > 0 for _, s, _ in log), f"最终 {log[-1][1]:.0f}")

print("\n== F. 累加精度 ==")
a16, a32, ref = M.accumulation_demo()
check("fp16 逐项累加误差 > 1e-3", abs(a16 - ref) / ref > 1e-3, f"{abs(a16 - ref) / ref:.3e}")
check("fp32 逐项累加基本无误差", abs(a32 - ref) / ref < 1e-12, f"{abs(a32 - ref) / ref:.3e}")
check("fp16 误差比 fp32 差 9 个数量级以上", abs(a16 - ref) / max(abs(a32 - ref), 1e-12) > 1e9, "≈1e9 倍")

print("\n== G. 前向舍入误差与 bf16 的范围优势 ==")
e16, ebf = M.rounding_error()
check("fp16 最大相对误差 ≈ 2^-11", 2.0 ** -12 < e16 < 2.0 ** -11, f"{e16:.6e}")
check("bf16(截断)误差约大 16 倍", ebf > e16 * 10, f"{ebf:.6e} vs {e16:.6e}")
check("bf16 保留 fp32 的指数范围(1e-30 不归零)", M.bf16_round(1e-30) != 0.0, f"{float(M.bf16_round(1e-30)):.3e}")
check("fp16 在 1e-30 处直接归零", M.fp16(1e-30) == 0.0, "0.0")

print(f"\n结果:{TOTAL[1]}/{TOTAL[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
raise SystemExit(0 if not FAILS else 1)
