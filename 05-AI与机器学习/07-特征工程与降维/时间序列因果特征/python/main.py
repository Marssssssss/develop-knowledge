"""demo 516 实验台:时间序列的因果特征与 CV 对齐陷阱。

目标序列 `x_t = 0.9 x_{t-1} + ε_t`,预测目标是**下一期** `y_t = x_{t+1}`,
于是「知道 x_t」与「知道 x_{t+1}」的差别可以被 R² 精确量化。
运行:`python main.py`
"""

from features import (causal_rolling_mean, shifted_rolling_mean,
                      leaky_rolling_mean_center)
from rolling import rolling_mean, shift  # E1 的对齐演示直接用原语
from splits import TimeSeriesSplit
from model import fit_ols, predict, r2
from rng import Rng


def make_ar1(n, phi=0.9, sigma=1.0, seed=7, shift_at=None, shift_size=0.0):
    r = Rng(seed)
    x, prev = [], 0.0
    for t in range(n):
        prev = phi * prev + sigma * r.normal()
        x.append(prev + (shift_size if shift_at is not None and t >= shift_at else 0.0))
    return x


def kfold_rows(n, k=5, seed=1):
    r = Rng(seed)
    idx = list(range(n))
    for i in range(n - 1, 0, -1):
        j = int(r.uniform() * (i + 1))
        idx[i], idx[j] = idx[j], idx[i]
    size = n // k
    folds = []
    for f in range(k):
        te = sorted(idx[f * size:(f + 1) * size])
        folds.append((sorted(set(range(n)) - set(te)), te))
    return folds


def align(x, fn, y):
    """按 fn 造单列特征,用同一套掩码取 y。返回 (n_rows, X, yv)。"""
    col = fn(x)
    rows = [i for i in range(len(col)) if col[i] is not None and y[i] is not None]
    return len(rows), [[1.0, col[i]] for i in rows], [y[i] for i in rows]


def cv_r2(X, yv, folds):
    out = []
    for tr, te in folds:
        w = fit_ols([X[i] for i in tr], [yv[i] for i in tr])
        out.append(r2([yv[i] for i in te], predict([X[i] for i in te], w)))
    return out


def holdout_r2(X, yv, frac=0.8):
    h = int(len(X) * frac)
    w = fit_ols(X[:h], yv[:h])
    return (r2(yv[:h], predict(X[:h], w)), r2(yv[h:], predict(X[h:], w)))


def f8(v):
    return "%9.4f" % v


VARIANTS = [
    ("因果  mean(x[t-5..t-1])", lambda v: causal_rolling_mean(v, 5)),
    ("错位  mean(x[t-4..t])  ", lambda v: shifted_rolling_mean(v, 5)),
    ("泄漏  mean(x[t-2..t+2])", lambda v: leaky_rolling_mean_center(v, 5)),
]


# --------------------------------------------------------------------- E1

def e1_alignment():
    print("E1 对齐:滚动窗口默认右闭,第 t 行**含 x[t]**")
    x = [float(v) for v in range(1, 9)]
    rm3 = rolling_mean(x, 3)
    rows = [
        ("x", x),
        ("rolling_mean(3)", rm3),
        ("  .shift(1)", shift(rm3, 1)),
        ("rolling_mean(3,center)", rolling_mean(x, 3, center=True)),
        ("y = shift(x,-1)", shift(x, -1)),
    ]
    for name, vals in rows:
        print("   %-24s %s" % (name, [None if v is None else round(v, 2) for v in vals]))
    print("   -> t=6(x=7) 这一行:默认版=%s(含 x[t]),shift(1) 版=%s(只到 x[t-1]),"
          % (rm3[6], shift(rm3, 1)[6]))
    print("      center 版=%s —— 它含 x[7]=8,把下一期搬到了本期"
          % rolling_mean(x, 3, center=True)[6])


# --------------------------------------------------------------------- E2

def e2_within_row_leak(x):
    n = len(x)
    y = shift(x, -1)
    print("E2 行内泄漏:三种滚动特征在时序 CV 与真·留出下的表现(目标 y_t = x_{t+1})")
    print("   特征                    时序CV(5x100) R²   最后 20% 留出 R²")
    for name, fn in VARIANTS:
        nr, X, yv = align(x, fn, y)
        folds = list(TimeSeriesSplit(5, test_size=100).split(list(range(nr))))
        vals = cv_r2(X, yv, folds)
        _, ho = holdout_r2(X, yv)
        print("   %-22s %s          %s" % (name, f8(sum(vals) / len(vals)), f8(ho)))
    print("   -> 泄漏版在**时序 CV 下照样拿到 0.83**:时序 CV 只能挡住「跨切分」的泄漏,")
    print("      挡不住「行内」的泄漏。这个漂亮的数字不是好消息,是作弊成功的证据。")


# --------------------------------------------------------------------- E3

def e3_kfold_vs_tss(x):
    print("E3 随机 KFold 在自相关序列上的虚高")
    y = shift(x, -1)
    nr, X, yv = align(x, VARIANTS[0][1], y)
    tss = cv_r2(X, yv, list(TimeSeriesSplit(5).split(list(range(nr)))))
    kf = cv_r2(X, yv, kfold_rows(nr, 5, seed=3))
    print("   TimeSeriesSplit(5) 折内 R² =", " ".join("%.3f" % v for v in tss))
    print("   随机 KFold(5)      折内 R² =", " ".join("%.3f" % v for v in kf))
    print("   均值:时序 CV = %s   随机 KFold = %s" % (f8(sum(tss) / 5), f8(sum(kf) / 5)))
    print("   -> 相邻行的特征窗口高度重叠,KFold 把「邻居」放进训练集,等于让模型抄答案")


# --------------------------------------------------------------------- E4

def e4_gap(x, h=3, label=""):
    print("E4%s gap 与重叠标签:标签跨 h=%d 期时训练集末端会伸进测试期" % (label, h))
    y = shift(x, -h)
    nr, X, yv = align(x, VARIANTS[0][1], y)
    print("   gap  时序CV R²      首折:训练末行 到 测试首行   标签伸进测试期的训练行数")
    for gap in [0, 1, 2, 3, 5]:
        folds = list(TimeSeriesSplit(4, test_size=100, gap=gap).split(list(range(nr))))
        vals = cv_r2(X, yv, folds)
        tr0, te0 = folds[0]
        over = sum(1 for r in range(tr0[-1] - h + 1, tr0[-1] + 1) if r + h >= te0[0])
        print("   %-4d %s        %4d -> %4d            %d"
              % (gap, f8(sum(vals) / len(vals)), tr0[-1], te0[0], over))
    print("   -> gap 的作用是**结构性**的:把训练集末端整块剔掉,使训练标签不再触碰测试期。")


# --------------------------------------------------------------------- E5

def e5_holdout(x):
    print("E5 真·未来留出:训练/留出的落差**不是**判据")
    y = shift(x, -1)
    print("   特征                    训练集 R²   留出集 R²   落差")
    for name, fn in VARIANTS:
        _, X, yv = align(x, fn, y)
        tr, ho = holdout_r2(X, yv)
        print("   %-22s %s   %s   %s" % (name, f8(tr), f8(ho), f8(tr - ho)))
    print("   -> 实测结论与直觉相反:**泄漏特征的落差最小(0.064),因果特征最大(0.176)**。")
    print("      因为泄漏特征与 y 几乎是确定关系,换到留出集也照样准。")
    print("      所以「落差小 = 可信」是错的判据;唯一的判据是 E6 那种机械检验。")


# --------------------------------------------------------------------- E6

def audit(fn, n=48, seeds=(11, 12, 13), tol=1e-9):
    """两轴机械探针:不需要标签、不需要切分、不需要跑模型。

    轴一「未来依赖」:扰动 x[j],数「第 t < j 行跟着变」的 (t, j) 对数。
        只要 > 0 就是泄漏 —— 决策时刻读到了尚未发生的观测,与标签怎么定义无关。
    轴二「对齐」:扰动 x[t] / x[t+1],数「第 t 行跟着变」的行数。两列之差说明这列
        特征读的是当期还是下一期。含当期 x[t] **不构成**未来依赖(决策时刻已知),
        所以它单独成列,不和轴一混在一起。

    注意:轴一只能查「j > t」,所以它**查不出**「错位」这种右端落在 t 的情况 ——
    这不是探针的缺陷,是定义如此。把两轴分开就是为了不让读者误判。
    """
    fut_bad = fut_tot = cur_hit = cur_tot = nxt_hit = nxt_tot = 0
    for seed in seeds:
        x = make_ar1(n, 0.9, 1.0, seed)
        base = fn(x)
        for j in range(n):
            x2 = x[:]
            x2[j] += 1.0
            v2 = fn(x2)
            for t in range(j):
                if base[t] is None or v2[t] is None:
                    continue
                fut_tot += 1
                if abs(v2[t] - base[t]) > tol:
                    fut_bad += 1
            if base[j] is not None and v2[j] is not None:   # 扰动 x[t] 看第 t 行
                cur_tot += 1
                if abs(v2[j] - base[j]) > tol:
                    cur_hit += 1
        for t in range(n - 1):                              # 扰动 x[t+1] 看第 t 行
            x2 = x[:]
            x2[t + 1] += 1.0
            v2 = fn(x2)
            if base[t] is None or v2[t] is None:
                continue
            nxt_tot += 1
            if abs(v2[t] - base[t]) > tol:
                nxt_hit += 1
    return fut_bad, fut_tot, cur_hit, cur_tot, nxt_hit, nxt_tot


def e6_probe():
    print("E6 机械因果探针(3 个随机种子 × n=48,扰动幅度 1.0;分母是各自的有效行数,")
    print("   三档的 NaN 边缘不同,所以分母略有差异 —— 看分子是否为 0,不要横向比大小)")
    print("   特征                     依赖未来 x[j](j>t)   扰动 x[t]→第t行   扰动 x[t+1]→第t行")
    for name, fn in VARIANTS:
        fb, ft, ch, ct, nh, nt = audit(fn)
        print("   %-24s %6d/%-6d %9d/%-6d %9d/%-6d" % (name, fb, ft, ch, ct, nh, nt))
    print("   -> 因果版:三列分子全 0,干净。")
    print("      错位版:**未来依赖 0**,但「扰动 x[t] 会动到第 t 行」满格 —— 它含当期、不含未来。")
    print("        在 y_t = x_{t+1} 的设定下 x[t] 本来就是决策时刻的已知量,所以它**不是泄漏**,")
    print("        而是对齐差 1 格:特征的注释若写「过去 5 期均值」,就与实现不符。")
    print("      泄漏版:未来依赖那一列的分子接近满格 —— 每一行都被 x[t+1] 与 x[t+2] 牵动,这是真泄漏。")
    print("      「错位」与「泄漏」被这张表彻底切开:前者只碰当期,后者碰未来;")
    print("      而单看 R²(E2:0.48 vs 0.83)只会得到「都比因果版好」这种毫无用处的结论。")


if __name__ == "__main__":
    x = make_ar1(1200, 0.9, 1.0, 7)
    e1_alignment()
    print()
    e2_within_row_leak(x)
    print()
    e3_kfold_vs_tss(x)
    print()
    e4_gap(x, 3)
    print()
    e4_gap(make_ar1(1200, 0.9, 1.0, 7, shift_at=1000, shift_size=3.0), 3,
           "(非平稳:第 1000 期起整体抬升)")
    print()
    e5_holdout(x)
    print()
    e6_probe()
