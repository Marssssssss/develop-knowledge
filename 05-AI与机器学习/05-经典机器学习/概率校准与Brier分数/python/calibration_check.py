# -*- coding: utf-8 -*-
"""概率校准自检:Brier 的三项分解是否恒等、"低 Brier ≠ 校准好"是否可复现、
Platt 的目标平滑与初值公式、sigmoid 保序 / isotonic 产生并列、temperature 不改 accuracy。

反例优先:把"校准好"直接断言成"Brier 低"是最常见的误用,所以 C 段专门造一个
"Brier 更低但 REL 更高"的样本对,让文档里那句话变成可执行的事实。
"""
import math

import calibration as C

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


def rnd(seed):
    st = seed

    def f():
        nonlocal st
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (st >> 11) / float(1 << 53)
    return f


def main():
    print("=" * 74)
    print("概率校准与 Brier 分数 —— 自检")
    print("=" * 74)

    # A. Brier 分解
    print("\n【A】Murphy 分解:BS = REL − RES + UNC")
    r = rnd(1)
    ok_id = True
    for _ in range(200):
        y = [1 if r() < 0.4 else 0 for _ in range(40)]
        p = [int(r() * 10) / 10.0 + 0.05 for _ in range(40)]   # 箱内常数(分解的适用前提)
        rel, res, unc = C.brier_decomposition(y, p)
        if abs((rel - res + unc) - C.brier(y, p)) > 1e-12:
            ok_id = False
    check("A1 箱内预测为常数时,分解恒等式 200 组逐组成立", ok_id, "容差 1e-12")
    yv = [1 if r() < 0.4 else 0 for _ in range(60)]
    pv = [r() ** 2 for _ in range(60)]                          # 箱内有波动
    rel, res, unc = C.brier_decomposition(yv, pv)
    # 把 BS 写成 p_i = p̄_b + δ_i 后展开,三项分解之外还剩
    #   (1/N)·Σ_b Σ_{i∈b} [δ_i² − 2·δ_i·(y_i − ō_b)]
    bins = {}
    for pi, yi in zip(pv, yv):
        bins.setdefault(min(int(pi * 10), 9), []).append((pi, yi))
    extra = 0.0
    for mem in bins.values():
        pbar = sum(m[0] for m in mem) / len(mem)
        obar = sum(m[1] for m in mem) / len(mem)
        for pi, yi in mem:
            d = pi - pbar
            extra += d * d - 2 * d * (yi - obar)
    extra /= len(pv)
    check("A2 箱内预测有波动时,差值恰等于展开后的余项 Σ[δ²−2δ(y−ō_b)]/N",
          abs(C.brier(yv, pv) - (rel - res + unc) - extra) < 1e-12, f"余项={extra:.6e}")
    y1 = [1 if r() < 0.4 else 0 for _ in range(60)]
    p1 = [int(r() * 10) / 10.0 + 0.05 for _ in range(60)]
    p2 = [min(1.0, v * 1.3) for v in p1]
    check("A3 UNC 只由数据正例率决定,与预测无关",
          abs(C.brier_decomposition(y1, p1)[2] - C.brier_decomposition(y1, p2)[2]) < 1e-12,
          f"ō(1−ō) = {C.brier_decomposition(y1, p1)[2]:.6f}")

    # B. 校准的定义
    print("\n【B】校准的定义:预测值 = 该组的真实正例率")
    yb, pb = [], []
    for frac, cnt in ((0.1, 40), (0.8, 40), (0.5, 40)):
        yb += [1] * int(frac * cnt) + [0] * (cnt - int(frac * cnt))
        pb += [frac] * cnt
    rel, res, _ = C.brier_decomposition(yb, pb, n_bins=10)
    check("B1 分组常量预测等于组内正例率时 REL ≈ 0", rel < 1e-12, f"REL={rel:.3e}")
    check("B2 此时 RES > 0(分组确实携带信息)", res > 0.05, f"RES={res:.6f}")
    curve = C.calibration_curve(yb, pb, n_bins=10)
    check("B3 校准曲线落在 y = x 上", all(abs(x - yy) < 1e-12 for x, yy, _ in curve),
          f"点={[(round(x, 3), round(yy, 3)) for x, yy, _ in curve]}")

    # C. 低 Brier ≠ 校准好
    print("\n【C】文档原话:更低的 Brier 不代表校准更好")
    # A:过度自信(真实率 0.7/0.3,却报 0.80/0.20)但分辨力极强
    yA = [1] * 140 + [0] * 60 + [1] * 60 + [0] * 140
    pA = [0.80] * 200 + [0.20] * 200
    # B:完全校准但分辨力很弱(真实率 0.55/0.45,如实报 0.55/0.45)
    yB = [1] * 110 + [0] * 90 + [1] * 90 + [0] * 110
    pB = [0.55] * 200 + [0.45] * 200
    bA, bB = C.brier(yA, pA), C.brier(yB, pB)
    relA, resA, _ = C.brier_decomposition(yA, pA)
    relB, resB, _ = C.brier_decomposition(yB, pB)
    check("C1 过度自信但分辨力强的模型 Brier 更低", bA < bB, f"{bA:.5f} vs {bB:.5f}")
    check("C2 但它的可靠性项 REL 更差(这正是文档要警告的事)", relA > relB,
          f"REL A={relA:.5f} vs B={relB:.5f}")
    check("C3 便宜占在分辨力项 RES 上:A 的 RES 是 B 的 10 倍以上", resA > 10 * resB,
          f"RES A={resA:.5f} vs B={resB:.5f}")

    # D. Platt 的目标平滑与初值
    print("\n【D】Platt 平滑目标与初值(sklearn `_sigmoid_calibration`)")
    yd = [1] * 30 + [0] * 70
    T = C.platt_targets(yd)
    check("D1 T[y>0] == (N_pos+1)/(N_pos+2) = 31/32", abs(T[0] - 31 / 32) < 1e-15,
          f"{T[0]:.9f}")
    check("D2 T[y≤0] == 1/(N_neg+2) = 1/72", abs(T[-1] - 1 / 72) < 1e-15, f"{T[-1]:.9f}")
    A0, B0 = C.platt_init(yd)
    check("D3 AB0 = [0, log((N_neg+1)/(N_pos+1))] = log(71/31)",
          A0 == 0.0 and abs(B0 - math.log(71 / 31)) < 1e-15, f"B0={B0:.9f}")
    check("D4 未超阈值时不缩放(scale == 1)", C.sigmoid_scale([1.0, 2.0])[1] == 1.0, "")
    check("D5 |f| 超过阈值 30 时按最大值整体缩放",
          abs(C.sigmoid_scale([1.0, 50000.0])[1] - 50000.0) < 1e-9, "")
    check("D6 阈值边界是 >=30:29.9 不缩放、30.0 缩放",
          C.sigmoid_scale([29.9])[1] == 1.0 and C.sigmoid_scale([30.0])[1] == 30.0, "")

    # E. sigmoid 保序 vs isotonic 产生并列
    print("\n【E】sigmoid 严格单调(保 AUC),isotonic 会引入并列")
    r3 = rnd(9)
    ye = [1 if r3() < 0.5 else 0 for _ in range(120)]
    fe = [(1.2 if yi else -1.2) + r3() * 1.5 for yi in ye]
    pe = [1 / (1 + math.exp(-v)) for v in fe]
    ps = C.SigmoidCalibration().fit(fe, ye).predict(fe)
    check("E1 sigmoid 校准后 AUC 完全不变(严格单调 ⇒ 排序不变)",
          abs(C.auc(ye, pe) - C.auc(ye, ps)) < 1e-12,
          f"{C.auc(ye, pe):.6f} vs {C.auc(ye, ps):.6f}")
    iso = C.IsotonicRegression().fit(fe, ye)
    fs = sorted(fe)
    pis = iso.predict(fs)
    check("E2 isotonic 的输出是阶跃函数 → 出现并列",
          len(pis) - len(set(round(v, 12) for v in pis)) > 0,
          f"并列数={len(pis) - len(set(round(v, 12) for v in pis))}")
    check("E3 按 f 升序预测时输出单调不降",
          all(pis[i] <= pis[i + 1] + 1e-12 for i in range(len(pis) - 1)), "")
    check("E4 isotonic 的拟合值落在 [0,1] 内(它是概率)",
          all(-1e-12 <= v <= 1 + 1e-12 for v in pis), "")

    # F. temperature scaling
    print("\n【F】temperature scaling:T 不改变 argmax ⇒ 不改变 accuracy")
    # 标签 20% 翻转:z=+2 组真实正例率 0.8,而 σ(2)=0.881 ⇒ **过度自信**
    zf = [2.0] * 100 + [-2.0] * 100
    yf = [1] * 80 + [0] * 20 + [1] * 20 + [0] * 80
    p_raw = [1 / (1 + math.exp(-z)) for z in zf]
    check("F0 原始概率高于真实正例率(过度自信)", p_raw[0] > 0.8 + 0.05,
          f"σ(2)={p_raw[0]:.4f} vs 真实率 0.80")
    hard_raw = [1 if z > 0 else 0 for z in zf]
    T_opt, _ = C.fit_temperature(zf, yf)
    pT = C.temperature_scale(zf, T_opt)
    check("F1 温度缩放前后硬预测完全一致(accuracy 不变)",
          hard_raw == [1 if v > 0.5 else 0 for v in pT],
          f"accuracy={C.accuracy(yf, hard_raw):.4f}")
    check("F2 过度自信模型上学出的 T > 1(把分布拉软)", T_opt > 1.0, f"T={T_opt:.4f}")
    rel_raw = C.brier_decomposition(yf, p_raw)[0]
    rel_T = C.brier_decomposition(yf, pT)[0]
    check("F3 校准后可靠性项 REL 下降", rel_T < rel_raw, f"REL {rel_raw:.6f} → {rel_T:.6f}")
    check("F4 T=1 时就是原始 sigmoid",
          all(abs(a - b) < 1e-15 for a, b in zip(C.temperature_scale(zf, 1.0), p_raw)), "")

    # G. 校准曲线
    print("\n【G】校准曲线(reliability diagram)")
    cg = C.calibration_curve(yf, p_raw, n_bins=5)
    # 典型"sigmoid 形"曲线:高分段**过度**自信(真实率 < 预测),低分段**不够**自信(真实率 > 预测)
    check("G1 高分段真实正例率 < 预测值、低分段真实正例率 > 预测值(sigmoid 形曲线)",
          all((yy < x) if x > 0.5 else (yy > x) for x, yy, _ in cg),
          f"{[(round(x, 4), round(yy, 4), n) for x, yy, n in cg]}")
    ch = C.calibration_curve(yf, pT, n_bins=5)
    dev_b = sum(n * (x - yy) ** 2 for x, yy, n in cg) / len(yf)
    dev_a = sum(n * (x - yy) ** 2 for x, yy, n in ch) / len(yf)
    check("G2 校准后曲线整体贴近 y = x(加权偏差下降)", dev_a < dev_b,
          f"{dev_b:.6f} → {dev_a:.6f}")

    print("\n" + "-" * 74)
    print(f"断言 {TOTAL[1]}/{TOTAL[0]} 通过")
    if FAILS:
        print("失败项:" + ", ".join(FAILS))
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
