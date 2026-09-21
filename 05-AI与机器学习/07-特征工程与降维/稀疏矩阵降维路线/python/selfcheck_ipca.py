"""demo 515 自检(IncrementalPCA / Youngs-Cramer 一路)。纯标准库,直接 `python selfcheck_ipca.py`。"""

from linalg import Rng, svd_jacobi
from ipca import IncrementalPCA, gen_batches, incremental_mean_and_var

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


def vmax(u, v):
    return max(abs(a - b) for a, b in zip(u, v))


def mat_abs_diff(A, B):
    return max(abs(abs(A[i][j]) - abs(B[i][j])) for i in range(len(A))
               for j in range(len(A[0])))


def make_data(n=60, p=6, seed=21):
    r = Rng(seed)
    return [[2.0 + r.normal() * (1.0 + 0.3 * j) for j in range(p)] for _ in range(n)]


def col_mean(X):
    n, p = len(X), len(X[0])
    return [sum(X[i][j] for i in range(n)) / n for j in range(p)]


def col_var(X):
    n, p = len(X), len(X[0])
    mu = col_mean(X)
    return [sum((X[i][j] - mu[j]) ** 2 for i in range(n)) / n for j in range(p)]


# ============================================================ 1. gen_batches
# 官方 doctest 里的四个例子必须逐字对上
ok("gen_batches(7,3) == [(0,3),(3,6),(6,7)]",
   list(gen_batches(7, 3)) == [(0, 3), (3, 6), (6, 7)])
ok("gen_batches(6,3) == [(0,3),(3,6)]", list(gen_batches(6, 3)) == [(0, 3), (3, 6)])
ok("gen_batches(2,3) == [(0,2)]", list(gen_batches(2, 3)) == [(0, 2)])
ok("gen_batches(7,3,min=2) 尾部并进最后一块",
   list(gen_batches(7, 3, 2)) == [(0, 3), (3, 7)])
ok("min_batch_size=0 与不传等价",
   list(gen_batches(7, 3, 0)) == list(gen_batches(7, 3)))
# 覆盖性:所有切片首尾相接且恰好覆盖 [0, n)
for (n_, bs, mb) in [(60, 7, 3), (61, 30, 5), (20, 10, 10), (5, 5, 0), (100, 7, 3)]:
    sl = list(gen_batches(n_, bs, mb))
    ok("gen_batches 覆盖完整 n=%d bs=%d mb=%d" % (n_, bs, mb),
       sl[0][0] == 0 and sl[-1][1] == n_ and all(sl[i][1] == sl[i + 1][0]
                                                for i in range(len(sl) - 1)))
    ok("gen_batches 每块非空 n=%d bs=%d mb=%d" % (n_, bs, mb),
       all(a < b for a, b in sl))
# 负向:min_batch_size 大于 batch_size 时官方会直接拒绝;本实现不校验,
# 但尾部合并规则仍保证「要么整块要么并尾」——断言不会吐出小于 bs 的中间块
sl = list(gen_batches(100, 10, 10))
ok("min==bs 时除末块外都是整块", all(b - a == 10 for a, b in sl[:-1]))

# ============================================================ 2. 增量均值/方差
X1 = make_data(20, 4, seed=1)
m1, v1, c1 = incremental_mean_and_var(X1, [0.0] * 4, [0.0] * 4, [0] * 4)
close("首块均值 == 列均值", vmax(m1, col_mean(X1)), 0.0, 1e-12)
close("首块方差 == 总体方差(ddof=0)", vmax(v1, col_var(X1)), 0.0, 1e-12)
ok("首块计数 == 行数", c1 == [20] * 4)
ok("方差里没有 NaN(zeros 分支确实被走到)",
   all(x == x for x in v1))
# 负向:不做 zeros 保护时,官方的 last_sum/last_over_new_count 是 0/0。
# numpy 给 nan,纯 Python 直接抛 —— 这正是该分支不可省的原因。
try:
    _ = (0.0 * 0) / (0 / 20)
    ok("未加 zeros 保护时本应抛 ZeroDivisionError", False)
except ZeroDivisionError:
    ok("纯 Python 下 0/0 直接抛 ZeroDivisionError,故 zeros 分支不可省", True)

X2b = make_data(15, 4, seed=2)
m2, v2, c2 = incremental_mean_and_var(X2b, m1, v1, c1)      # 只喂第 2 批
X1b = X1 + X2b                                              # 35 行
m3, v3, c3 = incremental_mean_and_var(X1b, [0.0] * 4, [0.0] * 4, [0] * 4)
close("两步增量 == 一次性全量(均值)", vmax(m2, m3), 0.0, 1e-11)
close("两步增量 == 一次性全量(方差)", vmax(v2, v3), 0.0, 1e-9)
ok("计数累加正确", c2 == [35] * 4)

mN, vN, cN = incremental_mean_and_var(X1, [0.0] * 4, None, [0] * 4)
ok("last_variance=None 时方差返回 None", vN is None)
close("last_variance=None 时均值仍照算", vmax(mN, col_mean(X1)), 0.0, 1e-12)

# 均值精确性:与顺序无关的对称统计量
Xsh = list(reversed(X1))
msh, _, _ = incremental_mean_and_var(Xsh, [0.0] * 4, [0.0] * 4, [0] * 4)
close("均值与行序无关", vmax(msh, m1), 0.0, 1e-15)

# 数值稳定性:1e8 偏移下一趟法崩、增量法与两趟法逐位一致
r = Rng(9)
big = [[1.0e8 + r.normal() for _ in range(3)] for _ in range(400)]
onepass = [sum(big[i][j] ** 2 for i in range(400)) / 400
           - (sum(big[i][j] for i in range(400)) / 400) ** 2 for j in range(3)]
twopass = col_var(big)
mean, var, cnt = [0.0] * 3, [0.0] * 3, 0
for i in range(0, 400, 25):
    mean, var, cntv = incremental_mean_and_var(big[i:i + 25], mean, var, [cnt] * 3)
    cnt = cntv[0]
ok("1e8 偏移下一趟法确实崩(负方差或差一个量级)",
   min(onepass) < 0.5 or max(abs(o - t) for o, t in zip(onepass, twopass)) > 0.5)
# 两种稳定算法在 1e8 偏移下只能一致到 ~1e-8(这是浮点本身的极限),
# 但与一趟法的崩溃量级相比,差了两个数量级以上 —— 这才是稳定性证据。
gap_inc = max(abs(v - t) for v, t in zip(var, twopass))
gap_one = max(abs(o - t) for o, t in zip(onepass, twopass))
ok("增量法与两趟法一致到 1e-6 以内", gap_inc < 1e-6)
ok("增量法/两趟法的差比一趟法的崩坏小两个数量级以上", gap_one > 100.0 * gap_inc)
close("增量法 == 两趟法(列0)", var[0], twopass[0], 1e-6)
close("增量法 == 两趟法(列1)", var[1], twopass[1], 1e-6)
close("增量法 == 两趟法(列2)", var[2], twopass[2], 1e-6)

# ============================================================ 3. IncrementalPCA
data = make_data(60, 6)
p = 6
k = 3

# 单块 == 精确 PCA(对已中心化的数据)
ip = IncrementalPCA(n_components=k, batch_size=60).fit([row[:] for row in data])
Xc = [[data[i][j] - col_mean(data)[j] for j in range(p)] for i in range(60)]
_, Sex, Vtex = svd_jacobi(Xc)
close("单块:均值 == 列均值", vmax(ip.mean_, col_mean(data)), 0.0, 1e-11)
close("单块:方差 == 总体方差", vmax(ip.var_, col_var(data)), 0.0, 1e-9)
close("单块:singular_values_ == 中心化数据的前 k 个奇异值",
      vmax(ip.singular_values_, Sex[:k]), 0.0, 1e-9)
close("单块:components_ == 中心化数据的右奇异向量",
      mat_abs_diff(ip.components_, Vtex[:k]), 0.0, 1e-9)
close("单块:explained_variance_ == S²/(n−1)",
      vmax(ip.explained_variance_, [s * s / 59.0 for s in Sex[:k]]), 0.0, 1e-9)
ok("单块:noise_variance_ == 尾部 explained_variance 均值",
   abs(ip.noise_variance_ -
       sum(s * s / 59.0 for s in Sex[k:]) / len(Sex[k:])) < 1e-9)
ok("单块:n_samples_seen_ == 60", ip.n_samples_seen_ == 60)
ok("单块:batch_size 显式给出时 batch_size_ 就是它", ip.batch_size_ == 60)

# batch_size=None → 5 * n_features(1.9.1 的值)
ipd = IncrementalPCA(n_components=k).fit([row[:] for row in data])
ok("batch_size=None → batch_size_ == 5 * n_features", ipd.batch_size_ == 5 * p)

# transform == (X - mean_) @ components_.T
Z = ip.transform([row[:] for row in data])
man = [[sum((data[i][j] - ip.mean_[j]) * ip.components_[c][j] for j in range(p))
        for c in range(k)] for i in range(60)]
close("transform == (X − mean_) @ components_.T", max(abs(Z[i][c] - man[i][c])
                                                    for i in range(60) for c in range(k)),
      0.0, 1e-12)
ok("transform 形状", len(Z) == 60 and len(Z[0]) == k)
close("fit_transform == fit 后的 transform",
      max(abs(a - b) for ra, rb in zip(ip.fit_transform([r_[:] for r_ in data]), Z)
          for a, b in zip(ra, rb)), 0.0, 1e-12)

# 多次 partial_fit 累加计数
ipm = IncrementalPCA(n_components=k, batch_size=15)
seen = []
for a in range(0, 60, 15):
    ipm.partial_fit([row[:] for row in data[a:a + 15]])
    seen.append(ipm.n_samples_seen_)
ok("partial_fit 逐块累加 n_samples_seen_", seen == [15, 30, 45, 60])

# noise_variance_ == 0 的边界:n_components_ 落在 (n_samples, n_features) 里
ipf = IncrementalPCA(n_components=6, batch_size=60).fit([row[:] for row in data])
ok("n_components_ == n_features 时 noise_variance_ == 0.0", ipf.noise_variance_ == 0.0)

# ---- batch_size 敏感度:是真实的近似误差,不是浮点噪声
ref = IncrementalPCA(n_components=k, batch_size=60).fit([row[:] for row in data])
for bs in [4, 7, 13, 20, 37]:
    m = IncrementalPCA(n_components=k, batch_size=bs).fit([row[:] for row in data])
    dmean = vmax(m.mean_, ref.mean_)
    dS = vmax(m.singular_values_, ref.singular_values_)
    ok("batch_size=%d 均值仍是精确的(<1e-12)" % bs, dmean < 1e-12)
    ok("batch_size=%d 奇异值差异 > 1e-3(近似精度确实受块大小影响)" % bs, dS > 1e-3)
ok("块越小偏差一般越大(bs=4 的 ΔS 大于 bs=37)",
   vmax(IncrementalPCA(n_components=k, batch_size=4).fit([r_[:] for r_ in data]).singular_values_,
        ref.singular_values_) >
   vmax(IncrementalPCA(n_components=k, batch_size=37).fit([r_[:] for r_ in data]).singular_values_,
        ref.singular_values_))

# ---- 行序敏感度:均值恒精确,分量会变
r2 = Rng(1)
idx = list(range(60))
for i in range(59, 0, -1):
    j = int(r2.uniform() * (i + 1))
    idx[i], idx[j] = idx[j], idx[i]
mm = IncrementalPCA(n_components=k, batch_size=17).fit([data[i][:] for i in idx])
ref17 = IncrementalPCA(n_components=k, batch_size=17).fit([row[:] for row in data])
ok("打乱后均值仍精确(<1e-12)", vmax(mm.mean_, ref17.mean_) < 1e-12)
ok("打乱后分量会变(>1e-3)", mat_abs_diff(mm.components_, ref17.components_) > 1e-3)
ok("打乱不改变 n_samples_seen_", mm.n_samples_seen_ == 60)

# ---- 错误路径
try:
    IncrementalPCA(n_components=8, batch_size=30).fit([row[:] for row in data])
    ok("n_components > n_features 应报错", False)
except ValueError as e:
    ok("n_components > n_features 报 ValueError", "invalid for n_features" in str(e))
try:
    IncrementalPCA(n_components=5).partial_fit([row[:] for row in data[:3]])
    ok("首块 n_components > n_samples 应报错", False)
except ValueError as e:
    ok("首块 n_components > n_samples 报 ValueError", "first partial_fit" in str(e))
try:
    ipc = IncrementalPCA(n_components=3, batch_size=20)
    ipc.partial_fit([row[:] for row in data[:20]])
    ipc.partial_fit([[1.0, 2.0, 3.0, 4.0, 5.0] for _ in range(20)])   # 特征数从 6 变 5
    ok("特征数变化应报错", False)
except ValueError as e:
    ok("特征数在两次 partial_fit 之间变化时报 ValueError",
       "expecting" in str(e) or "changed" in str(e))

print("PASS %d" % PASS)
if FAIL:
    print("FAIL %d" % len(FAIL))
    for f in FAIL:
        print("  -", f)
