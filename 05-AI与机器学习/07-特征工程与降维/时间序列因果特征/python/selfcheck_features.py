"""features.py + main.audit 自检(纯标准库,离线可跑)。

这套自检的核心是**支持集**(support set):扰动 x[j],看第 i 行的值有没有变,
就得到"第 i 行到底读了哪些下标"。有了它,"因果/错位/泄漏"不再是文字描述:

    因果 shift(rolling_mean(x,5),1) → 支持集 {i-5..i-1}     未来下标数 0
    错位 rolling_mean(x,5)          → 支持集 {i-4..i}       未来下标数 0
    泄漏 rolling_mean(x,5,center=1) → 支持集 {i-2..i+2}     未来下标数 2  ← 真泄漏
    泄漏 shift(rolling_mean(x,5),-1)→ 支持集 {i-3..i+1}     未来下标数 1  ← 真泄漏

「错位」未来下标数为 0 这一点是反直觉的关键:它含当期但不含未来,在 y_t = x_{t+1}
的设定下**不是**泄漏,只是对齐/注释与实现不符。

防循环:期望下标是手写的;**同时**给支持集探针加了两个负控(常量函数→∅、读全序列→全集),
并且断言四种构造的支持集两两不同 —— 否则"探针恒返回全集"也能让断言全绿。

运行:`python selfcheck_features.py`  期望末行 `PASS n / FAIL 0`
"""

from features import (causal_rolling_mean, shifted_rolling_mean,
                      leaky_rolling_mean_center, leaky_rolling_mean_shift_neg,
                      expanding_mean_causal, lag, build_matrix,
                      standardize_fit, standardize_apply)
from rolling import shift
from main import audit, make_ar1, VARIANTS

PASS = 0
FAIL = 0
FAILED = []


def ok(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(name)


N = 24


def support(fn, i, n=N, delta=1000.0, tol=1e-9):
    """扰动每个 x[j],收集会改变第 i 行的 j。第 i 行本身为空则返回 None。"""
    x = [float((j * 11) % 5) * 1.3 + j * 0.41 for j in range(n)]
    b = fn(x)[i]
    if b is None:
        return None
    out = set()
    for j in range(n):
        x2 = x[:]
        x2[j] += delta
        v = fn(x2)[i]
        if v is None or abs(v - b) > tol:
            out.add(j)
    return out


# ------------------------------------------------------- 负控:探针本身可信吗

for i in (0, 10, 23):
    ok(support(lambda v: [1.0] * len(v), i) == set(),
       "负控-常量函数 i=%d 支持集应为空" % i)
    ok(support(lambda v: [len(v) * 1.0 + sum(v)] * len(v), i) == set(range(N)),
       "负控-读全序列 i=%d 支持集应为全集" % i)
ok(support(lambda v: [v[0]] * len(v), 10) == {0},
   "正控-只读 x[0] 的函数支持集应是 {0}(证明探针能给单点集)")

# ------------------------------------------------------- 四个构造的支持集

I = 10
sup = {
    "因果": support(lambda v: causal_rolling_mean(v, 5), I),
    "错位": support(lambda v: shifted_rolling_mean(v, 5), I),
    "泄漏-center": support(lambda v: leaky_rolling_mean_center(v, 5), I),
    "泄漏-shift(-1)": support(lambda v: leaky_rolling_mean_shift_neg(v, 5), I),
}
ok(sup["因果"] == {5, 6, 7, 8, 9}, "因果支持集 i=10 应为 {5..9}, mine=%s" % sorted(sup["因果"]))
ok(sup["错位"] == {6, 7, 8, 9, 10}, "错位支持集 i=10 应为 {6..10}, mine=%s" % sorted(sup["错位"]))
ok(sup["泄漏-center"] == {8, 9, 10, 11, 12},
   "center 支持集 i=10 应为 {8..12}, mine=%s" % sorted(sup["泄漏-center"]))
ok(sup["泄漏-shift(-1)"] == {7, 8, 9, 10, 11},
   "shift(-1) 支持集 i=10 应为 {7..11}, mine=%s" % sorted(sup["泄漏-shift(-1)"]))

ok(len({frozenset(s) for s in sup.values()}) == 4,
   "四个构造的支持集必须两两不同(否则断言是空的)")

# 未来下标数:这是把"错位"和"泄漏"区分开的唯一一刀
future = {k: len([j for j in s if j > I]) for k, s in sup.items()}
ok(future["因果"] == 0, "因果:不得读到未来")
ok(future["错位"] == 0, "错位:含当期但**不读未来** —— 它不是泄漏")
ok(future["泄漏-center"] == 2, "center:读到 i+1 与 i+2 两个未来位")
ok(future["泄漏-shift(-1)"] == 1, "shift(-1):读到 i+1 一个未来位")
ok(future["错位"] < future["泄漏-center"], "错位≠泄漏:这一条断言就是全部论据")

# shift_by 越大越保守;window 不变时左端跟着左移
ok(support(lambda v: causal_rolling_mean(v, 5, 2), I) == {4, 5, 6, 7, 8},
   "shift_by=2 支持集 i=10 应为 {4..8}")
ok(support(lambda v: causal_rolling_mean(v, 5, 1), I) == sup["因果"],
   "shift_by=1 与主用法的支持集应一致")
for i in (6, 10, 15):     # 因果版最要紧的一条:右端必须停在 i-1,绝不碰自己
    s = support(lambda v: causal_rolling_mean(v, 5, 1), i)
    ok(i not in s and max(s) == i - 1,
       "因果版第 %d 行不得读 x[%d] 本身(右端须停在 i-1), mine=%s" % (i, i, sorted(s)))

# expanding 因果版同样只看过去
ok(support(lambda v: expanding_mean_causal(v, 1), I) == set(range(0, I)),
   "expanding 因果版支持集应为 {0..i-1}")
ok(support(lambda v: lag(v, 1), I) == {I - 1}, "lag(1) 支持集应为 {i-1}")

# ------------------------------------------------------- build_matrix 同掩码

rows, X = build_matrix(
    [list(range(20))],
    [(list(range(20)), lambda v: shifted_rolling_mean(v, 3)),        # None 于 i<2
     (list(range(20)), lambda v: causal_rolling_mean(v, 3))])        # None 于 i<3
ok(rows == list(range(3, 20)),
   "build_matrix 只保留全列非空的行(取交集),得到 %s" % (rows[:5],))
ok(len(X) == len(rows), "X 行数与 rows 一致")
ok(all(r[0] == 1.0 for r in X), "首列应为截距 1.0")
ok(all(r[1] is not None and r[2] is not None for r in X), "保留行不得含 None")
ok(rows == sorted(rows) and len(set(rows)) == len(rows), "rows 应严格递增")

# 三列不同掩码:交集应取最靠后的起点、最靠前的终点
#   shifted(w=3) 非空于 i>=2;causal(w=3,shift1) 非空于 i>=3;
#   center(w=3)  非空于 i in [1,18](i=19 时右端被裁到 cnt=2 < min_periods=3)
rows3, X3 = build_matrix(
    [list(range(20))],
    [(list(range(20)), lambda v: shifted_rolling_mean(v, 3)),
     (list(range(20)), lambda v: causal_rolling_mean(v, 3)),
     (list(range(20)), lambda v: leaky_rolling_mean_center(v, 3))])
ok(rows3[0] == 3 and rows3[-1] == 18,
   "三列取交集应为 3..18, mine=%s" % (rows3[:3] + rows3[-2:],))
ok(len(X3[0]) == 4, "截距 + 3 列 = 4 列")

# ------------------------------------------------------- 标准化

Xm = [[1.0, float(i)] for i in range(12)]
mu, sd = standardize_fit(Xm, 1)
ok(abs(mu - 5.5) < 1e-12, "standardize_fit 均值应为 5.5")
ok(abs(sd - (11.9166666666667) ** 0.5) < 1e-9, "standardize_fit 用总体标准差")
Zs = standardize_apply(Xm, 1, mu, sd)
zm = sum(r[1] for r in Zs) / len(Zs)
zs = (sum((r[1] - zm) ** 2 for r in Zs) / len(Zs)) ** 0.5
ok(abs(zm) < 1e-12, "标准化后均值应为 0")
ok(abs(zs - 1.0) < 1e-12, "标准化后总体标准差应为 1")

Xc = [[1.0, 3.0], [1.0, 3.0]]
mu2, sd2 = standardize_fit(Xc, 1)
ok(sd2 == 0.0, "常量列的标准差为 0")
Zc = standardize_apply(Xc, 1, mu2, sd2)
ok(Zc == Xc, "sd==0 分支应原样返回")
ok(Zc is not Xc and Zc[0] is not Xc[0], "应返回副本,不是同一个 list")
Zc[0][1] = 999.0
ok(Xc[0][1] == 3.0, "改动返回值不得污染入参")

# ------------------------------------------------------- main.audit 的两轴

def future_pairs(fn, x, tol=1e-9):
    """与 audit 的轴一同义,但返回具体的 (t, j) 集合,便于断言闭式。"""
    base = fn(x)
    out = set()
    for j in range(len(x)):
        x2 = x[:]
        x2[j] += 1.0
        v2 = fn(x2)
        for t in range(j):
            if base[t] is not None and v2[t] is not None and abs(v2[t] - base[t]) > tol:
                out.add((t, j))
    return out


n = 48
x = make_ar1(n, 0.9, 1.0, 11)
pc = future_pairs(lambda v: leaky_rolling_mean_center(v, 5), x)
want_c = {(j - 1, j) for j in range(3, 47)} | {(j - 2, j) for j in range(4, 48)}
ok(pc == want_c, "center 的未来依赖对恰好是 {t=j-1} ∪ {t=j-2}, mine=%d 对 want=%d 对"
   % (len(pc), len(want_c)))
ok(len(pc) == 88, "center 单种子违例数应为 88")

pn = future_pairs(lambda v: leaky_rolling_mean_shift_neg(v, 5), x)
want_n = {(j - 1, j) for j in range(4, 48)}
ok(pn == want_n, "shift(-1) 的未来依赖对恰好是 {t=j-1}, mine=%d 对 want=%d 对"
   % (len(pn), len(want_n)))

ok(future_pairs(lambda v: causal_rolling_mean(v, 5), x) == set(),
   "因果版未来依赖集必须为空")
ok(future_pairs(lambda v: shifted_rolling_mean(v, 5), x) == set(),
   "错位版未来依赖集必须为空(含当期 ≠ 读未来)")

# audit 的三轴聚合:签名 + 负控
fb, ft, ch, ct, nh, nt = audit(lambda v: causal_rolling_mean(v, 5))
ok((fb, ch, nh) == (0, 0, 0), "audit 因果版三轴分子应全 0,实际 %s" % ((fb, ch, nh),))
fb, ft, ch, ct, nh, nt = audit(lambda v: shifted_rolling_mean(v, 5))
ok(fb == 0 and ch == ct and ct > 0 and nh == 0,
   "audit 错位版:未来 0、含当期满格、不含下一期,实际 %s" % ((fb, ch, ct, nh),))
fb, ft, ch, ct, nh, nt = audit(lambda v: leaky_rolling_mean_center(v, 5))
ok(fb == 264, "audit 泄漏-center 未来违例 = 3 种子 × 88 = 264,实际 %d" % fb)
ok(fb < ft, "违例数必须远小于总对数(否则探针恒真,等于没测), %d vs %d" % (fb, ft))
ok(ch == ct and nh == nt, "center 版当期与下一期都应满格")
fb, ft, ch, ct, nh, nt = audit(lambda v: leaky_rolling_mean_shift_neg(v, 5))
ok(fb == 132, "audit 泄漏-shift(-1) 未来违例 = 3 × 44 = 132,实际 %d" % fb)
ok(nh == nt and ch == ct, "shift(-1) 版当期与下一期都应满格")

# audit 的负控:必须能判另两个"显然该被抓/不该被抓"的函数
ok(audit(lambda v: shift(v, -1))[0] > 0, "负控:shift(-1) 必须被判定为读未来")
ok(audit(lambda v: lag(v, 1))[0] == 0, "负控:lag(1) 必须被判为不读未来")
ok(audit(lambda v: [0.0] * len(v))[0] == 0, "负控:常量函数无未来依赖(探针不恒真)")

# main.py 的 VARIANTS 必须与 features.py 的构造一致(防标签漂移)
ok(support(VARIANTS[0][1], I) == sup["因果"], "VARIANTS[0] 应对应因果版")
ok(support(VARIANTS[1][1], I) == sup["错位"], "VARIANTS[1] 应对应错位版")
ok(support(VARIANTS[2][1], I) == sup["泄漏-center"], "VARIANTS[2] 应对应 center 泄漏版")
ok(len({v[0].strip() for v in VARIANTS}) == 3, "三条 VARIANTS 的名称必须互不相同")
ok("x[t-5..t-1]" in VARIANTS[0][0] and "x[t-4..t]" in VARIANTS[1][0]
   and "x[t-2..t+2]" in VARIANTS[2][0],
   "VARIANTS 的文字标签必须与实际窗口区间相符(标签漂移会让 README 说谎)")

if __name__ == "__main__":
    print("selfcheck_features: PASS %d / FAIL %d" % (PASS, FAIL))
    for f in FAILED[:20]:
        print("  FAIL:", f)
    raise SystemExit(1 if FAIL else 0)
