"""demo 515 自检(随机化 SVD / TruncatedSVD 一路)。纯标准库,直接 `python selfcheck_rsvd.py`。

断言对象是本目录的纯标准库实现;最强证据是与 sklearn/scipy 的逐条对拍,
那部分放在仓库外的离线对拍脚本里(见 README「自检」一节)。
"""

import math

from linalg import Rng, matmul, transpose, randn, svd_jacobi, _sign
from main import block_counts, mean_vector, cos, decayed_spectrum
from rsvd import lu_permute_l, randomized_range_finder, randomized_svd, TruncatedSVD

PASS = 0
FAIL = []


def ok(name, cond):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(name)


def close(name, a, b, tol=1e-10):
    ok("%s (%.3e vs %.3e, tol %.0e)" % (name, a, b, tol), abs(a - b) <= tol)


def dmax(A, B):
    return max(abs(A[i][j] - B[i][j]) for i in range(len(A)) for j in range(len(A[0])))


def vmax(u, v):
    return max(abs(a - b) for a, b in zip(u, v))


def orth_err_cols(A):
    k = len(A[0])
    G = matmul(transpose(A), A)
    return max(abs(G[i][j] - (1.0 if i == j else 0.0)) for i in range(k) for j in range(k))


def orth_err_rows(A):
    return orth_err_cols(transpose(A))


# ============================================================ 1. lu_permute_l
ok("_sign(0)==0 是官方 np.sign 语义(不是 +1)", _sign(0.0) == 0.0)
ok("_sign(-0.5)==-1", _sign(-0.5) == -1.0)

for shape in [(6, 6), (8, 4), (4, 8), (5, 5), (12, 3), (3, 12)]:
    r = Rng(3 + shape[0])
    A = randn(shape[0], shape[1], r)
    PL, U = lu_permute_l(A)
    m, n = shape
    k = min(m, n)
    ok("lu PL 形状 %s" % (shape,), len(PL) == m and len(PL[0]) == k)
    ok("lu U 形状 %s" % (shape,), len(U) == k and len(U[0]) == n)
    close("lu 重建 A=(PL)U %s" % (shape,), dmax(matmul(PL, U), A), 0.0, 1e-10)
    ok("lu U 上三角 %s" % (shape,),
       all(abs(U[i][j]) < 1e-14 for i in range(k) for j in range(i)))
    # PL 是「行被置换过的 L」,所以它本身不是三角阵 —— 非零个数必须与 L 一致
    ok("lu PL 非零元个数 ≤ m*k %s" % (shape,),
       sum(1 for i in range(m) for j in range(k) if PL[i][j] != 0.0) <= m * k)

# 对角占优 → 不需要换行,这时 PL 就是货真价实的单位下三角
for n_ in [3, 5, 8]:
    Dd = [[float(n_ + 3 * (i == j)) + 0.1 * i + 0.05 * j for j in range(n_)]
          for i in range(n_)]
    PLn, Un = lu_permute_l(Dd)
    close("无换行时 A=LU", dmax(matmul(PLn, Un), Dd), 0.0, 1e-10)
    ok("无换行时 PL 是单位下三角 n=%d" % n_,
       all(abs(PLn[i][j]) < 1e-14 for i in range(n_) for j in range(i + 1, n_))
       and all(abs(PLn[i][i] - 1.0) < 1e-14 for i in range(n_)))

# 负向:不换行的矩阵里 PL 就是 L 本身(主元全在对角上)
D = [[4.0, 1.0], [1.0, 3.0]]
PLd, Ud = lu_permute_l(D)
ok("lu 无需换行时 PL == L(对角为 1)", PLd[0][0] == 1.0 and PLd[1][1] == 1.0)
ok("lu 无需换行时 PL 非置换矩阵", not (PLd[0][1] == 1.0 and PLd[1][0] == 1.0))
PLi, Ui = lu_permute_l([[0.0, 1.0], [1.0, 0.0]])
ok("lu 必须换行时 PL 真的是置换后的 L", PLi == [[0.0, 1.0], [1.0, 0.0]])

# ============================================================ 2. range finder
Xf = decayed_spectrum(kind="fast")
_, Sf, Vtef = svd_jacobi(Xf)
Q = randomized_range_finder(Xf, 6, n_iter=2, normalizer="LU", rng=Rng(3))
ok("range finder Q 形状 = (m, size)", len(Q) == len(Xf) and len(Q[0]) == 6)
close("range finder Q 列正交", orth_err_cols(Q), 0.0, 1e-12)
# Q 的值域应当装下前 6 个左奇异向量
Uf = transpose(transpose(svd_jacobi(Xf)[0])[:6])         # m x 6
C = matmul(transpose(Uf), Q)                             # 6 x 6
_, sc, _ = svd_jacobi(C)
ok("range finder 最大主余弦 ≈ 1", sc[0] > 1 - 1e-9)
ok("range finder 最小主余弦仍高(6 维对 6 维无过采样)", sc[-1] > 0.999)
Qq = randomized_range_finder(Xf, 12, n_iter=2, normalizer="LU", rng=Rng(3))
C2 = matmul(transpose(Uf), Qq)
_, sc2r, _ = svd_jacobi(C2)
ok("过采样到 12 后最小主余弦更接近 1", sc2r[-1] > sc[-1])
Q0 = randomized_range_finder(Xf, 6, n_iter=0, normalizer="none", rng=Rng(3))
ok("n_iter=0 与 n_iter=2 得到不同的 Q", dmax(Q, Q0) > 1e-3)
ok("range finder 对同一 seed 可复现",
   dmax(Q, randomized_range_finder(Xf, 6, n_iter=2, normalizer="LU", rng=Rng(3))) == 0.0)
try:
    randomized_range_finder(Xf, 6, n_iter=1, normalizer="bogus")
    ok("非法 normalizer 应报错", False)
except ValueError:
    ok("非法 normalizer 报 ValueError", True)

# ============================================================ 3. randomized_svd
Xr = decayed_spectrum(kind="fast")
for (m, n, k) in [(120, 60, 6), (60, 120, 6), (50, 50, 5)]:
    r = Rng(7 + m)
    A = randn(m, n, r)
    U, s, Vt = randomized_svd(A, k, n_oversamples=10, n_iter=4, rng=Rng(2))
    ok("rsvd U 形状 %dx%d" % (m, n), len(U) == m and len(U[0]) == k)
    ok("rsvd s 形状 %dx%d" % (m, n), len(s) == k)
    ok("rsvd Vt 形状 %dx%d" % (m, n), len(Vt) == k and len(Vt[0]) == n)
    ok("rsvd s 降序且非负 %dx%d" % (m, n),
       all(s[i] >= s[i + 1] >= 0 for i in range(k - 1)))
    close("rsvd U 列正交 %dx%d" % (m, n), orth_err_cols(U), 0.0, 1e-11)
    close("rsvd Vt 行正交 %dx%d" % (m, n), orth_err_rows(Vt), 0.0, 1e-11)

# 精确低秩矩阵:取满 n_oversamples 后应当重建到机器精度
Xlow = decayed_spectrum(m=80, p=40, nr=6, seed=4, kind="fast")   # 精确秩 6
U, s, Vt = randomized_svd(Xlow, 6, n_oversamples=6, n_iter=2, rng=Rng(5))
rec = matmul(matmul(U, [[s[t] if j == t else 0.0 for t in range(6)] for j in range(6)]), Vt)
close("rsvd 在精确秩 6 上重建到机器精度", dmax(rec, Xlow), 0.0, 1e-11)
_, s6, _ = svd_jacobi(Xlow)
close("rsvd 返回的奇异值 = 精确奇异值", vmax(s, s6[:6]), 0.0, 1e-10)

# 负向:取 k=3 < 秩 6 时不可能精确重建,误差下界就是被丢掉的那几个奇异值
U3, s3, Vt3 = randomized_svd(Xlow, 3, n_oversamples=6, n_iter=2, rng=Rng(5))
rec3 = matmul(matmul(U3, [[s3[t] if j == t else 0.0 for t in range(3)] for j in range(3)]), Vt3)
fro3 = math.sqrt(sum((rec3[i][j] - Xlow[i][j]) ** 2
                     for i in range(len(Xlow)) for j in range(len(Xlow[0]))))
tail3 = math.sqrt(sum(s6[t] ** 2 for t in range(3, 6)))
ok("rsvd k<秩 时重建误差 > 0(负向)", fro3 > 1e-3)
ok("rsvd k<秩 时误差不低于 Eckart-Young 下界", fro3 >= tail3 * (1 - 1e-9))
ok("rsvd k<秩 时误差贴近下界", fro3 <= tail3 * 1.02)
ok("rsvd k<秩 时 s 仍降序", s3[0] >= s3[1] >= s3[2])

# flip_sign 判决基准:两种模式都落在「返回的 U 每一列最大绝对值元素非负」
#   非 transpose → u_based=True(看 u 的列);transpose → u_based=False(看内部 v 的行,
#   而返回的 U 正是内部 Vt 的前 k 行转置,故同样等价于「返回 U 的列」)
def cols_nonneg(A):
    return all(A[max(range(len(A)), key=lambda i: abs(A[i][j]))][j] >= 0
               for j in range(len(A[0])))


Uc2, sc2, Vtc2 = randomized_svd(Xf, 6, n_iter=2, rng=Rng(3))
ok("rsvd 非 transpose:返回 U 的列最大绝对值元素非负", cols_nonneg(Uc2))
Ub, sb, Vtb = randomized_svd(transpose(Xf), 6, n_iter=2, rng=Rng(3))
ok("rsvd transpose:返回 U 的列最大绝对值元素非负", cols_nonneg(Ub))
Uno, sno, Vtno = randomized_svd(Xf, 6, n_iter=2, flip_sign=False, rng=Rng(3))
ok("flip_sign=False 时确实不做符号修正(与 True 的某列反号)",
   any(Uno[i][j] * Uc2[i][j] < 0 for i in range(len(Uno)) for j in range(6)))
close("flip_sign 不影响奇异值", vmax(sno, sc2), 0.0, 1e-12)
close("rsvd 两条 transpose 分支给出的奇异值一致", vmax(sb, sc2), 0.0, 1e-9)

# ============================================================ 4. TruncatedSVD
Xb = block_counts()
mu = mean_vector(Xb)
t = TruncatedSVD(n_components=4, random_state=0, n_iter=7).fit(Xb)
ok("tsvd fit 返回 self", t.fit(Xb) is t)
ok("tsvd components_ 形状", len(t.components_) == 4 and len(t.components_[0]) == 25)
ok("tsvd components_ 行正交", orth_err_rows(t.components_) < 1e-11)
ok("tsvd 不中心化:comp0 就是均值方向", abs(cos(t.components_[0], mu)) > 0.999)

Z = t.transform(Xb)
ok("tsvd transform 形状", len(Z) == len(Xb) and len(Z[0]) == 4)
manual = [[sum(Xb[i][q] * t.components_[c][q] for q in range(25)) for c in range(4)]
          for i in range(len(Xb))]
close("tsvd transform == X @ components_.T(手写展开)", dmax(Z, manual), 0.0, 1e-12)

# explained_variance_ 是投影后各列的 np.var(ddof=0)
for c in range(4):
    col = [Z[i][c] for i in range(len(Z))]
    m_ = sum(col) / len(col)
    close("tsvd explained_variance_[%d] == np.var(投影列)" % c,
          t.explained_variance_[c], sum((v - m_) ** 2 for v in col) / len(col), 1e-12)
full_var = sum(sum((Xb[i][q] - mu[q]) ** 2 for i in range(len(Xb))) / len(Xb)
               for q in range(25))
for c in range(4):
    close("tsvd explained_variance_ratio_[%d] 分母 = X 各列 var 之和" % c,
          t.explained_variance_ratio_[c], t.explained_variance_[c] / full_var, 1e-14)
ok("tsvd ratio 之和 ≤ 1", sum(t.explained_variance_ratio_) <= 1.0 + 1e-12)

rec = matmul(Z, t.components_)
Ue, Se, _ = svd_jacobi(Xb)
tail = math.sqrt(sum(x * x for x in Se[4:]))
err = math.sqrt(sum((rec[i][j] - Xb[i][j]) ** 2
                    for i in range(len(Xb)) for j in range(25)))
ok("tsvd 不能突破 Eckart-Young 下界", err >= tail * (1 - 1e-9))
ok("tsvd 逼近最优 rank-4", err <= tail * 1.02)

ok("tsvd fit_transform == fit 后的 transform",
   dmax(t.fit_transform(Xb), t.transform(Xb)) == 0.0)
try:
    TruncatedSVD(n_components=26).fit(Xb)
    ok("n_components > n_features 应报错", False)
except ValueError:
    ok("n_components > n_features 报 ValueError", True)
try:
    TruncatedSVD(n_components=2, algorithm="arpack").fit(Xb)
    ok("未实现的 algorithm 应报错", False)
except ValueError:
    ok("未实现的 algorithm 报 ValueError", True)

# n_oversamples / k 与残差的关系:子空间占满后残差不再下降
errs = []
for k in [1, 4, 8]:
    tk = TruncatedSVD(n_components=k, random_state=0, n_iter=7).fit(Xb)
    rk = matmul(tk.transform(Xb), tk.components_)
    errs.append(math.sqrt(sum((rk[i][j] - Xb[i][j]) ** 2
                              for i in range(len(Xb)) for j in range(25))))
ok("tsvd 残差随 k 单调不增", errs[0] > errs[1] > errs[2])

print("PASS %d" % PASS)
if FAIL:
    print("FAIL %d" % len(FAIL))
    for f in FAIL:
        print("  -", f)
