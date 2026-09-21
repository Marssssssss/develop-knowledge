"""model.py + rng.py 自检(纯标准库,离线可跑)。

重点在两处**分支**:
  * `solve` 的部分主元:必须用一个"首元为 0"的矩阵来验,并附上 naive 无选主元的负控,
    证明"不选主元真的会崩"——否则选主元这段代码删掉也能通过。
  * `r2` 的 `ss_tot > 0` 分支:常量 y 走 else 返回 0.0;同时要证明 r2 **可以取负**,
    否则"1 - ss_res/ss_tot"里写死 max(...,0) 也看不出来。

运行:`python selfcheck_model.py`  期望末行 `PASS n / FAIL 0`
"""

import math

from model import fit_ols, predict, r2, mse, solve
from rng import Rng, randn

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


def close(a, b, name, tol=1e-9):
    ok(a is not None and b is not None and abs(a - b) <= tol,
       "%s (mine=%r, want=%r)" % (name, a, b))


# ------------------------------------------------------------------ solve

x = solve([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])
close(x[0], 1.0, "solve 2x2 手推解 x0", 1e-12)
close(x[1], 3.0, "solve 2x2 手推解 x1", 1e-12)

# 首元为 0 → 不选主元会除以 0
piv = solve([[0.0, 1.0], [1.0, 0.0]], [3.0, 7.0])
close(piv[0], 7.0, "选主元后 x0", 1e-12)
close(piv[1], 3.0, "选主元后 x1", 1e-12)


def naive_no_pivot(A, b):
    """故意不选主元的实现,用来反证"选主元不是摆设"。"""
    n = len(A)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for k in range(n):
        piv = M[k][k]
        if piv == 0.0:
            return None                      # 崩掉,返回 None 表示退化
        for i in range(k + 1, n):
            f = M[i][k] / piv
            for j in range(k + 1, n + 1):
                M[i][j] -= f * M[k][j]
    out = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n] - sum(M[i][j] * out[j] for j in range(i + 1, n))
        out[i] = s / M[i][i]
    return out


ok(naive_no_pivot([[0.0, 1.0], [1.0, 0.0]], [3.0, 7.0]) is None,
   "负控:naive 无选主元在同一矩阵上确实退化(None)")

# 3x3 手推:对角占优,解 = [1,2,3]
A3 = [[4.0, 1.0, 0.0], [1.0, 5.0, 1.0], [0.0, 1.0, 6.0]]
b3 = [A3[i][0] * 1.0 + A3[i][1] * 2.0 + A3[i][2] * 3.0 for i in range(3)]
x3 = solve(A3, b3)
close(x3[0], 1.0, "solve 3x3 x0", 1e-9)
close(x3[1], 2.0, "solve 3x3 x1", 1e-9)
close(x3[2], 3.0, "solve 3x3 x2", 1e-9)

# 部分主元在多步消元里也要起作用:第一个主元非零,但消元后第二个变 0
A4 = [[1.0, 1.0, 1.0], [1.0, 1.0, 2.0], [1.0, 2.0, 1.0]]
b4 = [6.0, 7.0, 8.0]                       # 解 [3,2,1]:z=1(式2-式1),y=2(式3-式1),x=3
x4 = solve(A4, b4)
close(x4[0], 3.0, "solve 需中途换行 x0", 1e-9)
close(x4[1], 2.0, "solve 需中途换行 x1", 1e-9)
close(x4[2], 1.0, "solve 需中途换行 x2", 1e-9)
ok(all(abs(v) < 1e3 for v in x4), "解应是合理量级(不是把某个 0 主元硬除成 inf)")

# ------------------------------------------------------------------ fit_ols

xs = [float(i) for i in range(1, 9)]
ys = [2.0 + 3.0 * v for v in xs]
X = [[1.0, v] for v in xs]
w = fit_ols(X, ys)
close(w[0], 2.0, "fit_ols 截距(无噪声,含 1e-8 岭)", 1e-4)
close(w[1], 3.0, "fit_ols 斜率(无噪声,含 1e-8 岭)", 1e-4)
ok(abs(w[0] - 2.0) + abs(w[1] - 3.0) < 1e-4, "无噪声时残差应可忽略")

# 共线设计:两列完全相同。有 l2 也必须是有限值
Xc = [[1.0, v, v] for v in xs]
wc = fit_ols(Xc, ys)
ok(all(math.isfinite(v) for v in wc), "共线设计的解必须有限,l2 的作用就是消掉奇异")
ok(r2(ys, predict(Xc, wc)) > 1.0 - 1e-6, "共线设计仍应能完美拟合")

# l2 关闭 → 结果与开启时不同(证明那段分支真的被执行)
wd = fit_ols(X, ys, l2=0.0)
ok(wd != w, "l2 参数确实进入计算(0.0 与 1e-8 结果不同)")

# 只用截距:拟合常量 → 截距 = 均值
ybar = [7.0] * 6
w1 = fit_ols([[1.0] for _ in range(6)], ybar)
close(w1[0], 7.0, "仅截距模型应给出均值", 1e-6)

# ------------------------------------------------------------------ predict / r2 / mse

close(predict([[1.0, 3.0], [1.0, 4.0]], [1.0, 2.0])[0], 7.0, "predict 手推")
close(predict([[1.0, 3.0], [1.0, 4.0]], [1.0, 2.0])[1], 9.0, "predict 手推第二行")

close(r2([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0, "完美预测 → 1.0")
close(r2([1.0, 2.0, 3.0], [2.0, 2.0, 2.0]), 0.0, "预测恒为均值 → 0.0")
# 反向预测:[1,2,3] vs [3,2,1] → ss_tot=2, ss_res=8 → 1-4 = -3
close(r2([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]), -3.0, "反相关预测 → 负数(证明没有 max(.,0) 截断)")
close(r2([5.0, 5.0, 5.0], [4.0, 5.0, 6.0]), 0.0, "常量 y 走 ss_tot<=0 分支返回 0.0")
ok(r2([5.0, 5.0, 5.0], [4.0, 5.0, 6.0]) == 0.0
   and r2([1.0, 2.0, 3.0], [2.0, 2.0, 2.0]) == 0.0,
   "两条路径都返回 0.0,不能靠 0.0 区分——所以上一对断言要成对读")

close(mse([1.0, 2.0], [1.0, 4.0]), 2.0, "mse 手推 (0+4)/2")
close(mse([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 0.0, "完美拟合 mse=0")

# 端到端:泄漏越大,R² 越高(方向性断言,只检查符号不看数值)
r = Rng(5)
noise = [0.3 * v for v in randn(60, r)]
seq = [math.sin(i * 0.3) + noise[i] for i in range(60)]
yt = [seq[i + 1] if i + 1 < 60 else None for i in range(60)]
rows = [i for i in range(59)]
causal = [sum(seq[i - 4:i]) / 4 for i in rows]        # 右端 t-1
leaky = [sum(seq[i - 1:i + 4]) / 4 for i in rows]      # 右端 t+3,含未来
Xc2 = [[1.0, causal[i]] for i in range(len(rows))]
Xl2 = [[1.0, leaky[i]] for i in range(len(rows))]
yv = [yt[i] for i in rows]
rc = r2(yv, predict(Xc2, fit_ols(Xc2, yv)))
rl = r2(yv, predict(Xl2, fit_ols(Xl2, yv)))
ok(rl > rc, "端到端:含未来的特征在训练集上 R² 必须更高 (%.4f > %.4f)" % (rl, rc))
ok(rl > 0.8 and rc < 0.5,
   "端到端量级:泄漏版应远高于因果版 (rc=%.4f rl=%.4f)" % (rc, rl))

# ------------------------------------------------------------------ rng

ok(Rng(1).u32() == 1015568748, "LCG 首值 = (1664525*1+1013904223) mod 2^32")
a = [Rng(42).uniform() for _ in range(1)]
b = [Rng(42).uniform() for _ in range(1)]
ok(a == b, "同种子必须可复现")
ok(Rng(42).uniform() != Rng(43).uniform(), "不同种子应给出不同值")
r2a = Rng(7)
seq1 = [r2a.uniform() for _ in range(500)]
seq2 = [r2a.uniform() for _ in range(500)]
ok(seq1 != seq2, "同实例连续抽样不应重复")
ok(all(0.0 <= v < 1.0 for v in seq1 + seq2), "uniform 落在 [0,1)")
r3 = Rng(9)
u = [r3.uniform() for _ in range(20000)]
mu = sum(u) / len(u)
var = sum((v - mu) ** 2 for v in u) / len(u)
ok(abs(mu - 0.5) < 0.02, "uniform 均值 20000 样本应接近 0.5 (%.4f)" % mu)
ok(0.07 < var < 0.10, "uniform 方差应接近 1/12=0.0833 (%.4f)" % var)
ok(len(randn(37, Rng(3))) == 37, "randn 长度")
r4 = Rng(11)
z = randn(20000, r4)
zm = sum(z) / len(z)
zv = sum((v - zm) ** 2 for v in z) / len(z)
ok(abs(zm) < 0.06, "Box-Muller 正态均值应接近 0 (%.4f)" % zm)
ok(0.93 < zv < 1.07, "Box-Muller 正态方差应接近 1 (%.4f)" % zv)

if __name__ == "__main__":
    print("selfcheck_model: PASS %d / FAIL %d" % (PASS, FAIL))
    for f in FAILED[:20]:
        print("  FAIL:", f)
    raise SystemExit(1 if FAIL else 0)
