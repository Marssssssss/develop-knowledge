"""残差连接自检:18 项断言。全绿才认为 demo 成立。

断言的是「方向性结论」而不是某次运行的精确数值 —— 例如恒等短路是否让
梯度剖面变平、深网络是否比浅网络更难训、shortcut 换成非恒等是否变差。
"""

import numpy as np

import residual as R

TOTAL = [0, 0]
FAILS = []
H = 48


def check(name, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        TOTAL[1] += 1
        print(f"  [PASS] {name}  {detail}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


print("== A. 解析反向正确性(中心差分) ==")
for kind, sc in (("plain", "identity"), ("res", "identity"), ("res", "scale"), ("res", "proj")):
    e = R.grad_check(kind, shortcut=sc)
    check(f"{kind}/{sc} 相对误差 < 1e-6", e < 1e-6, f"{e:.3e}")

print("\n== B. 梯度剖面:恒等短路把「1」加进反向链路 ==")
np_p, _ = R.signal_profile(32, H, "plain", seed=0)
np_r, _ = R.signal_profile(32, H, "res", seed=0)
rp, rr = np_p[0] / np_p[-1], np_r[0] / np_r[-1]
check("plain 首/末梯度 > 50(深层梯度被指数压制)", rp > 50, f"{rp:.3f}")
check("residual 首/末梯度 < 10(剖面被拉平)", rr < 10, f"{rr:.3f}")
check("两者相差一个数量级以上", rp / rr > 10, f"{rp / rr:.2f}x")
check("梯度全为正且有限", all(v > 0 and np.isfinite(v) for v in np_p + np_r), "无 nan/inf")

print("\n== C. 前向信号量级:residual 累积、plain 有界 ==")
_, fw_p = R.signal_profile(32, H, "plain", seed=0)
_, fw_r = R.signal_profile(32, H, "res", seed=0)
check("plain 的量级有界(0.2~1.5)", 0.2 < fw_p[-1] < 1.5, f"‖x32‖/‖x0‖={fw_p[-1]:.3f}")
check("residual 的量级随深度增长(>2)", fw_r[-1] > 2.0, f"‖x32‖/‖x0‖={fw_r[-1]:.3f}")
check("residual 的量级单调不减", all(fw_r[i + 1] >= fw_r[i] * 0.98 for i in range(31)), "逐个块检查")

print("\n== D. 退化问题:更深的 plain 网络训练损失反而更高 ==")
l0p2, lfp2 = R.train(2, H, "plain", steps=250, seed=0)
l0p32, lfp32 = R.train(32, H, "plain", steps=250, seed=0)
l0r2, lfr2 = R.train(2, H, "res", steps=250, seed=0)
l0r32, lfr32 = R.train(32, H, "res", steps=250, seed=0)
check("plain: depth32 终损失 > depth2 终损失", lfp32 > lfp2, f"{lfp2:.4f} vs {lfp32:.4f}")
check("residual: depth32 终损失 < depth2 终损失", lfr32 < lfr2, f"{lfr2:.4f} vs {lfr32:.4f}")
check("depth32 上 residual 显著优于 plain", lfr32 < lfp32 * 0.5, f"{lfp32:.4f} → {lfr32:.4f}")
check("两者都没有发散(有限值)", all(np.isfinite(v) for v in (lfp2, lfp32, lfr2, lfr32)), "无 nan")
check(
    "residual 的初始损失更大(残差流累积的代价)",
    l0r32 > l0p32,
    f"{l0p32:.1f} vs {l0r32:.1f}",
)

print("\n== E. shortcut 消融(恒等 vs 0.9h vs 倒序投影,depth=32,3 seed 中位数) ==")
res = {}
for sc in ("identity", "scale", "proj"):
    ratios, losses = [], []
    for seed in range(3):
        n_, _ = R.signal_profile(32, H, "res", shortcut=sc, seed=seed)
        ratios.append(n_[0] / n_[-1])
        losses.append(R.train(32, H, "res", steps=250, seed=seed, shortcut=sc)[1])
    res[sc] = (float(np.median(ratios)), float(np.median(losses)))
    print(f"  {sc:>8}: 首/末梯度 {res[sc][0]:.3f}   终损失 {res[sc][1]:.4f}")
check("恒等 shortcut 的终损失最低", res["identity"][1] < min(res["scale"][1], res["proj"][1]),
      f"{res['identity'][1]:.4f} < {res['scale'][1]:.4f} / {res['proj'][1]:.4f}")
check("恒等 shortcut 的梯度剖面最平坦(首/末比最大)", res["identity"][0] > max(res["scale"][0], res["proj"][0]),
      f"{res['identity'][0]:.3f}")
check("非恒等 shortcut 抑制了输入侧梯度(首/末比 ≤ 恒等)", res["scale"][0] < res["identity"][0], f"{res['scale'][0]:.3f}")
check("三种 shortcut 都不发散", all(np.isfinite(v[1]) for v in res.values()), "无 nan")

print(f"\n结果:{TOTAL[1]}/{TOTAL[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
raise SystemExit(0 if not FAILS else 1)
