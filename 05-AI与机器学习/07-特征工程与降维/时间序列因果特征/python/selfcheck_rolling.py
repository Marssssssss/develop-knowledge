"""rolling.py 自检(纯标准库,离线可跑)。

核心手法:**支持集探针**。扰动 x[j] 看第 i 行有没有变,就得到"第 i 行读了哪些下标"。
这比"读代码猜窗口"强得多 —— 它直接测出实现实际读了什么。

防循环:期望值**不是**用 rolling._window_bounds 算出来的,而是测试里手写的下标元组
      与**性质关系**(left 是 right 整体左移 1 格等)。另外给 support() 本身加了两个
      负控(常量函数→空集;读全序列→全集),否则"探针恒返回大集合"也能骗过断言。

运行:`python selfcheck_rolling.py`  期望末行 `PASS n / FAIL 0`
"""

import rolling as R

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


# ------------------------------------------------------------------ 探针

def support(fn, n, i, delta=1000.0, tol=1e-9):
    """返回"扰动 x[j] 会改变第 i 行"的 j 集合;第 i 行本身为空值则返回 None。"""
    x = [float((j * 13) % 7) * 1.5 + j * 0.37 for j in range(n)]
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


N = 12


def rm(w, center=False, closed=None, mp=1):
    return lambda v: R.rolling_mean(v, w, mp, center, closed)


# ---- 负控:证明探针能分辨"什么都没有"与"什么都有" ----
for i in (0, 5, 11):
    ok(support(lambda v: [0.0] * len(v), N, i) == set(),
       "负控-常量函数在 i=%d 的支持集应为空" % i)
    ok(support(lambda v: [sum(v) / len(v)] * len(v), N, i) == set(range(N)),
       "负控-读全序列在 i=%d 的支持集应为全集" % i)

# ---- 逐例:手写下标元组 ----
CASES = [
    # (w, center, closed, i, 期望支持集)
    (3, False, None, 5, {3, 4, 5}),
    (3, False, "right", 5, {3, 4, 5}),
    (3, False, "left", 5, {2, 3, 4}),
    (3, False, "both", 5, {2, 3, 4, 5}),
    (3, False, "neither", 5, {3, 4}),
    (3, True, None, 5, {4, 5, 6}),          # (3-1)//2 = 1
    (4, True, None, 5, {3, 4, 5, 6}),       # 偶数窗:右端只到 i+1
    (4, False, None, 5, {2, 3, 4, 5}),
    (5, True, None, 5, {3, 4, 5, 6, 7}),
    (1, False, None, 5, {5}),
    # 边界裁剪
    (3, False, None, 0, {0}),
    (3, False, None, 1, {0, 1}),
    (3, False, None, 11, {9, 10, 11}),
    (3, True, None, 11, {10, 11}),
    (5, False, None, 3, {0, 1, 2, 3}),
]
for w, c, cl, i, want in CASES:
    got = support(rm(w, c, cl), N, i)
    ok(got == want, "支持集 w=%d center=%s closed=%s i=%d: mine=%s want=%s"
       % (w, c, cl, i, sorted(got) if got else got, sorted(want)))

# ---- 性质:closed 四种取值只挪 ≤1 格,且四者互不相同(非空断言) ----
for i in (5, 6, 7):
    Rr = support(rm(5, False, "right"), N, i)
    Lf = support(rm(5, False, "left"), N, i)
    Bo = support(rm(5, False, "both"), N, i)
    Ne = support(rm(5, False, "neither"), N, i)
    ok(Lf == {j - 1 for j in Rr}, "i=%d left 应为 right 整体左移 1 格" % i)
    ok(len({frozenset(Rr), frozenset(Lf), frozenset(Bo), frozenset(Ne)}) == 4,
       "i=%d closed 四取值必须给出四个不同的支持集(否则断言是空的)" % i)
    # left=[start-1,end-1] 而 neither=[start,end-1] → 只差 start-1 这一个点
    ok(Lf == Ne | {min(Rr) - 1}, "i=%d left == neither ∪ {right 左端 - 1}" % i)
    ok(Bo == Rr | {min(Rr) - 1}, "i=%d both == right ∪ {right 左端 - 1}" % i)

# ---- 性质:center 只把末端右移 (w-1)//2,左端跟着走 ----
for w in (2, 3, 4, 5, 6):
    base = support(rm(w, False, None), N, 7)
    cen = support(rm(w, True, None), N, 7)
    ok(cen == {j + (w - 1) // 2 for j in base},
       "w=%d center 应等价于整体右移 %d 格" % (w, (w - 1) // 2))

# ---- 长度与空值位置 ----
xs = [1.0, 3.0, 2.0, 5.0, 4.0, 7.0, 6.0, 9.0, 8.0, 10.0, 11.0, 13.0]
ok(len(R.rolling_mean(xs, 3)) == len(xs), "输出长度应等于输入长度")
ok(R.rolling_mean(xs, 3)[:2] == [None, None], "w=3 时前 2 行应为 None(右闭)")
ok(R.rolling_sum(xs, 3)[2] is not None, "w=3 时第 3 行开始有值")

# ------------------------------------------------------------------ min_periods

try:
    R.rolling_sum(xs, 3, 4)
    ok(False, "min_periods > window 应抛 ValueError")
except ValueError as e:
    ok("4" in str(e) and "3" in str(e), "min_periods>window 的报错应含两个数字: %s" % e)

close(R.rolling_mean(xs, 4, min_periods=None)[3], sum(xs[0:4]) / 4,
      "整数窗口 min_periods 默认取 window")
ok(R.rolling_mean(xs, 4)[2] is None, "w=4 第 3 行不足 4 点应为 None")
ok(R.rolling_mean(xs, 4, min_periods=3)[2] is not None, "显式 min_periods=3 时第 3 行应有值")

# min_periods=0 + 空窗口:pandas 仍求值,sum→0.0,mean→NaN
ok(R.rolling_sum(xs, 1, min_periods=0, closed="neither")[0] == 0.0,
   "空窗口 sum 应为 0.0")
ok(R.rolling_mean(xs, 1, min_periods=0, closed="neither")[0] is None,
   "空窗口 mean 应为 None")
ok(R.rolling_min(xs, 1, min_periods=0, closed="neither")[0] is None,
   "空窗口 min 应为 None")
ok(R.rolling_sum(xs, 1, min_periods=1, closed="neither")[0] is None,
   "min_periods=1 时空窗口应为 None(与 mp=0 相反,证明分支真实存在)")

# ------------------------------------------------------------------ std 的 ddof

close(R.rolling_std([1.0, 2.0, 3.0], 3)[2], 1.0, "std w=3 ddof=1 → 1.0")
close(R.rolling_std([1.0, 2.0, 3.0], 3, ddof=0)[2], (2.0 / 3) ** 0.5,
      "std w=3 ddof=0 → sqrt(2/3)")
ok(R.rolling_std([5.0], 1)[0] is None, "w=1 且 ddof=1 时分母为 0 → None")
ok(R.rolling_std([5.0], 1, ddof=0)[0] == 0.0, "w=1 且 ddof=0 时 → 0.0(反证上一条不是恒真)")

# ------------------------------------------------------------------ 其它原语

close(R.rolling_count(xs, 3)[0], 1.0, "count 不做 min_periods 过滤,首行即 1")
close(R.rolling_count(xs, 5)[5], 5.0, "count w=5 满窗即 5")
close(R.rolling_count(xs, 5, closed="both")[5], 6.0, "closed=both 满窗 6 点")
close(R.rolling_count(xs, 5, closed="neither")[5], 4.0, "closed=neither 满窗 4 点")
close(R.rolling_count(xs, 3, closed="both")[0], 1.0, "左端裁剪后 closed=both 首行仍是 1 点")

y = R.shift(xs, 1)
ok(y[0] is None and y[1] == xs[0] and y[-1] == xs[-2], "shift(1) 向后挪一格")
ok(R.shift(xs, 0) == xs, "shift(0) 应等于原序列")
ok(R.shift(xs, -1)[-1] is None and R.shift(xs, -1)[0] == xs[1],
   "shift(-1) 向前挪(会引入未来)")

ok(R.diff(xs, 0) == [0.0] * len(xs), "diff(0) 恒为 0")
close(R.diff(xs, 1)[3], xs[3] - xs[2], "diff(1) 为相邻差")

z = [1.0, 2.0, 0.0, 4.0, 5.0]
p = R.pct_change(z, 1)
# p[i] = (z[i] - z[i-1]) / z[i-1] —— 分母是**前一期**,所以分母为 0 出现在 i=3(z[2]=0)
ok(p[3] is None, "分母(z[i-1])为 0 的位置应为 None")
ok(p[2] is not None and p[4] is not None, "分母非 0 的位置应有值(反证上一条)")
close(p[1], (2.0 - 1.0) / 1.0, "pct_change 第二行")
close(p[2], (0.0 - 2.0) / 2.0, "pct_change 分子为 0 时值应为 -1,不是 None")

ok(R.expanding_mean([1.0, 2.0, 3.0])[0] == 1.0,
   "expanding 默认 min_periods=1,首行即含当期")
ok(R.expanding_mean([1.0, 2.0, 3.0], min_periods=4) == [None] * 3,
   "min_periods 超长时全 None(反证上一条)")
close(R.expanding_sum(xs, 3)[2], sum(xs[:3]), "expanding_sum mp=3 首行")

try:
    R.rolling_mean(xs, 3, closed="bogus")
    ok(False, "非法 closed 应抛 ValueError")
except ValueError:
    ok(True, "非法 closed 抛 ValueError")

try:
    R.rolling_mean(xs, 0)
    ok(False, "window < 1 应抛 ValueError")
except ValueError:
    ok(True, "window < 1 抛 ValueError")

if __name__ == "__main__":
    print("selfcheck_rolling: PASS %d / FAIL %d" % (PASS, FAIL))
    for f in FAILED[:20]:
        print("  FAIL:", f)
    raise SystemExit(1 if FAIL else 0)
