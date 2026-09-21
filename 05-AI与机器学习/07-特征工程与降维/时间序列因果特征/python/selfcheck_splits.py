"""splits.py 自检(纯标准库,离线可跑)。

全部期望值都是**手推的下标元组**,不是从实现里反推出来的;负控用于证明"该失败的
前提确实成立"(例如:naive 无选主元消元在那个矩阵上真的会崩,所以"有选主元"不是摆设)。

运行:`python selfcheck_splits.py`  期望末行 `PASS n / FAIL 0`
"""

from splits import TimeSeriesSplit, rolling_origin_splits

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


def folds(obj, n):
    """返回 [(train, test), ...];正规化为 list 便于比较。"""
    return [(list(a), list(b)) for a, b in obj.split(list(range(n)))]


def raises(fn, needle, name):
    """注意:`split()` 是**生成器函数**,两条硬校验写在函数体里,不迭代就一行都不会执行。
    所以这里必须真的把生成器跑干 —— 只调用 `split()` 是拿不到异常的。"""
    try:
        for _ in fn():
            pass
    except ValueError as e:
        ok(needle in str(e), "%s(报错文案应含 %r,实际 %r)" % (name, needle, str(e)[:60]))
        return
    ok(False, "%s:应当抛 ValueError 却没抛" % name)


def no_raise(fn, name):
    try:
        for _ in fn():
            pass
        ok(True, name)
    except ValueError as e:
        ok(False, "%s 不应报错: %s" % (name, e))


# ----------------------------------------------------------- 手推的切分结果

fs = folds(TimeSeriesSplit(2, test_size=3), 10)
ok(fs == [([0, 1, 2, 3], [4, 5, 6]), (list(range(7)), [7, 8, 9])],
   "n=10 ns=2 ts=3 的手推结果, mine=%s" % (fs,))

fs = folds(TimeSeriesSplit(3, test_size=3), 12)
ok(fs == [([0, 1, 2], [3, 4, 5]), (list(range(6)), [6, 7, 8]),
          (list(range(9)), [9, 10, 11])],
   "n=12 ns=3 ts=3 的手推结果, mine=%s" % (fs,))

# test_size 默认 = n // (n_splits + 1)
fs = folds(TimeSeriesSplit(2), 10)
ok(fs[0][1] == [4, 5, 6], "ts 默认 10//3=3 且起点 10-2*3=4,首折测试集应为 [4,5,6], mine=%s" % (fs[0][1],))
ok(len(folds(TimeSeriesSplit(4), 10)[0][1]) == 2, "ts 默认 10//5=2")

# gap:只把训练集末端整块剔掉,不移动测试集
g0 = folds(TimeSeriesSplit(2, test_size=3, gap=0), 10)
g1 = folds(TimeSeriesSplit(2, test_size=3, gap=1), 10)
g2 = folds(TimeSeriesSplit(2, test_size=3, gap=2), 10)
ok([t for t, _ in g1] == [[0, 1, 2], list(range(6))], "gap=1 训练集砍掉尾部 1 个")
ok([t for t, _ in g2] == [[0, 1], list(range(5))], "gap=2 训练集砍掉尾部 2 个")
ok([te for _, te in g0] == [te for _, te in g1] == [te for _, te in g2],
   "gap 不动测试集(所以它治的是标签侧泄漏,不是特征侧)")

# max_train_size:只有严格小于 train_end 时才截成滑动窗口
ok([t for t, _ in folds(TimeSeriesSplit(2, test_size=3, max_train_size=2), 10)]
   == [[2, 3], [5, 6]], "mts=2 < train_end → 滑动窗口")
ok([t for t, _ in folds(TimeSeriesSplit(2, test_size=3, max_train_size=4), 10)]
   == [[0, 1, 2, 3], [3, 4, 5, 6]], "mts=4:首折 4<4 不成立 → 不截;次折 4<7 成立 → 截")
ok([t for t, _ in folds(TimeSeriesSplit(2, test_size=3, max_train_size=99), 10)]
   == [[0, 1, 2, 3], list(range(7))], "mts 很大时退化为无上限")

# ----------------------------------------------------------- 结构性质

for ns, ts in [(2, 3), (3, 2), (4, 2)]:
    n = 12
    fs = folds(TimeSeriesSplit(ns, test_size=ts), n)
    ok(len(fs) == ns, "ns=%d 应产出 %d 折" % (ns, ns))
    ends = [te[-1] for _, te in fs]
    ok(ends == sorted(ends), "测试折应按时间递增")
    ok(max(ends) == n - 1, "最后一折应触到序列末尾")
    flat = [i for _, te in fs for i in te]
    ok(len(flat) == len(set(flat)) == ns * ts, "测试集之间不得重叠且恰好铺满 ns*ts 个点")
    for tr, te in fs:
        ok(max(tr) < min(te), "训练集必须严格早于测试集")
        ok(tr == list(range(len(tr))), "训练集是无缝隙前缀")
    if ts is not None:
        sup = [len(tr) for tr, _ in fs]
        ok(sup == sorted(sup) and sup[-1] > sup[0], "训练集应随折数递增(超集链)")

ok(TimeSeriesSplit(3).get_n_splits() == 3, "get_n_splits 返回 n_splits")

# 负控:同参数下(不给 max_train_size)train 长度必须真的递增,否则上一条断言是空的
chain = [t for t, _ in folds(TimeSeriesSplit(3, test_size=2), 12)]
ok(all(set(a) < set(b) for a, b in zip(chain, chain[1:])),
   "无 max_train_size 时训练集应是严格递增的真超集链")

# ----------------------------------------------------------- 两条硬校验

raises(lambda: TimeSeriesSplit(5).split(list(range(5))), "Cannot have number of folds=6", "n_folds > n_samples")
raises(lambda: TimeSeriesSplit(2, test_size=5).split(list(range(10))),
       "Too many splits=2", "n - gap - ts*ns <= 0")
raises(lambda: TimeSeriesSplit(2, test_size=4, gap=2).split(list(range(10))),
       "Too many splits=2", "gap 参与第二条校验")

# 反证:边界恰好差 1 就必须放行(否则上面两条断言可能只是"什么都报错")
no_raise(lambda: TimeSeriesSplit(2, test_size=5).split(list(range(11))),
         "n=11 ts=5 差 1 分放行")
fs = folds(TimeSeriesSplit(2, test_size=5), 11)
ok(len(fs) == 2 and fs[0][1] == [1, 2, 3, 4, 5], "n=11 ts=5:首折测试集 [1..5], mine=%s" % (fs[0],))
no_raise(lambda: TimeSeriesSplit(1, test_size=1, gap=1).split(list(range(4))),
         "n=4 ns=1 ts=1 gap=1 放行")
ok(folds(TimeSeriesSplit(1, test_size=1, gap=1), 4) == [([0, 1], [3])],
   "n=4 ns=1 ts=1 gap=1 → 训练 [0,1] 测试 [3]")
no_raise(lambda: TimeSeriesSplit(1, test_size=1, gap=2).split(list(range(4))),
         "n=4 gap=2 恰好 4-2-1=1>0 放行")

# ----------------------------------------------------------- rolling_origin

ro = rolling_origin_splits(10, n_splits=3, horizon=2)
ok(ro == [([0, 1, 2, 3], [4, 5]), ([0, 1, 2, 3, 4, 5], [6, 7]),
          (list(range(8)), [8, 9])],
   "n=10 ns=3 h=2 的手推结果, mine=%s" % (ro,))
blocks = [te for _, te in ro]
ok([b[0] for b in blocks] == [4, 6, 8], "horizon=2 时测试块起点间隔为 2")
ok(all(len(b) == 2 for b in blocks), "每块长度等于 horizon")
ok(all(max(tr) < min(te) for tr, te in ro), "训练集必须早于测试集")

len1 = rolling_origin_splits(10, n_splits=3, horizon=2, min_train=7)
ok(len1 == [(list(range(8)), [8, 9])],
   "min_train=7 应剔掉前两折, mine=%s" % (len1,))
ok(len(rolling_origin_splits(10, n_splits=3, horizon=2, min_train=99)) == 0,
   "min_train 过大时一折都不剩(反证上一条确实在筛选)")
ok(len(rolling_origin_splits(10, n_splits=3, horizon=2, min_train=4)) == 3,
   "min_train=4 恰好放行首折(边界)")

# 它与 TimeSeriesSplit 的关系:同参数下**测试块划分完全一致**(horizon 就是 test_size)。
# 这一点必须断言出来,否则容易误以为两者在测不同的东西。
tss = folds(TimeSeriesSplit(3, test_size=2), 10)
ok([te for _, te in tss] == blocks, "h=test_size 时两者测试块划分应一致")
ok([tr for tr, _ in tss] == [tr for tr, _ in ro], "同参数下训练集也一致")

# 真正的差别一:数据不够时 TimeSeriesSplit **报错**,rolling_origin **静默少给几折**。
raises(lambda: TimeSeriesSplit(3, test_size=2).split(list(range(6))), "Too many splits=3",
       "n=6 ns=3 ts=2 数据不够")
ok(rolling_origin_splits(6, n_splits=3, horizon=2) == [([0, 1], [2, 3]), (list(range(4)), [4, 5])],
   "同数据下 rolling_origin 给 2 折(首折因 train_end<min_train 被剔)")

# 真正的差别二:返回值类型 —— 前者是生成器,后者是 list(可重复遍历)
g = TimeSeriesSplit(2, test_size=3).split(list(range(10)))
ok(list(g) != [] and list(g) == [], "split() 是生成器,遍历一次就空了")
ok(rolling_origin_splits(10, 3, 2) == rolling_origin_splits(10, 3, 2),
   "rolling_origin 是 list,可重复遍历")

# ------------------------------------------------- 官方 docstring 的数值锚点
# 以下四组期望值**逐字抄自** sklearn 1.9.1 `model_selection/_split.py` 里
# TimeSeriesSplit 的 docstring 示例输出,不是我推的 —— 这是本文件最硬的一组断言。

x12 = list(range(12))
# >>> TimeSeriesSplit(n_splits=3, test_size=2)
ok(folds(TimeSeriesSplit(3, test_size=2), 12)
   == [([0, 1, 2, 3, 4, 5], [6, 7]), ([0, 1, 2, 3, 4, 5, 6, 7], [8, 9]),
       (list(range(10)), [10, 11])],
   "官方示例 ns=3 ts=2:训练 6/8/10,测试 [6,7]/[8,9]/[10,11]")
# >>> TimeSeriesSplit(n_splits=3, test_size=2, gap=2)
ok(folds(TimeSeriesSplit(3, test_size=2, gap=2), 12)
   == [([0, 1, 2, 3], [6, 7]), ([0, 1, 2, 3, 4, 5], [8, 9]),
       ([0, 1, 2, 3, 4, 5, 6, 7], [10, 11])],
   "官方示例 gap=2:测试集不动,训练集各砍尾 2 个")
# >>> TimeSeriesSplit()  # n_splits=5,test_size=None,n=6
ok(folds(TimeSeriesSplit(5), 6)
   == [([0], [1]), ([0, 1], [2]), ([0, 1, 2], [3]), ([0, 1, 2, 3], [4]),
       ([0, 1, 2, 3, 4], [5])],
   "官方示例默认参数 n=6:test_size=6//6=1,训练集大小 1,2,3,4,5")
# notes 里的公式(注意 i 是 1-based,否则 i=0 会算出空训练集,与示例矛盾)
for i in range(1, 6):
    n = 6
    ns = 5
    want = i * (n // (ns + 1)) + n % (ns + 1)
    ok(len(folds(TimeSeriesSplit(ns), n)[i - 1][0]) == want,
       "官方 notes 公式 i=%d(1-based)→ 训练集大小 %d" % (i, want))

# 口径差异:sklearn 的 `n_splits >= 2` 是在父类 `_BaseKFold.__init__` 里查的(构造期),
# TimeSeriesSplit.split 本身**没有**这条校验。本 demo 是独立类,不继承该检查 ——
# 所以自检里能跑 ns=1 的边界用例,而 sklearn 会在构造时就报错。
ok(len(folds(TimeSeriesSplit(1, test_size=1), 4)) == 1,
   "本实现允许 ns=1(口径差异,非缺陷)")

if __name__ == "__main__":
    print("selfcheck_splits: PASS %d / FAIL %d" % (PASS, FAIL))
    for f in FAILED[:20]:
        print("  FAIL:", f)
    raise SystemExit(1 if FAIL else 0)
