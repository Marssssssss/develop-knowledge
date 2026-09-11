# 直方分位数估算 histogram_quantile —— demo (Python stdlib only)
# 依据 https://prometheus.io/docs/querying/functions/ 与
#     https://prometheus.io/docs/practices/histograms
import math


def repair_monotonic(counts):
    """官方单调修复: 先忽略相对差 < 1e-12 的微小下降, 再把非单调桶抬升为前桶值。"""
    fixed = list(counts)
    for i in range(1, len(fixed)):
        prev, cur = fixed[i - 1], fixed[i]
        if cur < prev:
            if (prev - cur) < 1e-12 * (prev + cur):
                fixed[i] = prev          # 浮点精度误差: 静默修复
            else:
                fixed[i] = prev          # 真实非单调: 强制抬升(实际实现会打 annotation)
    return fixed


def histogram_quantile(phi, les, counts):
    """Prometheus 语义: 累积桶 + 桶内线性插值 + 官方边界规则。
    les 按升序, 最高桶为 +Inf; counts 为累积计数。"""
    if math.isnan(phi):
        return math.nan
    if phi < 0:
        return -math.inf
    if phi > 1:
        return math.inf
    if len(les) < 2 or les[-1] != math.inf:
        return math.nan                    # 桶数 <2 或缺 +Inf
    counts = repair_monotonic(counts)
    rank = phi * counts[-1]
    for i, c in enumerate(counts):
        if c >= rank:                      # 第一个容纳 rank 的桶
            if math.isinf(les[i]):
                return les[i - 1]          # 最高桶 -> 次高桶上界(不外插)
            if i == 0:
                lower = 0.0 if les[i] > 0 else les[i]  # 最低桶下界假设为 0
                prev = 0.0
            else:
                lower, prev = les[i - 1], counts[i - 1]
            if c == prev:
                return lower               # 空桶防除零
            return lower + (les[i] - lower) * (rank - prev) / (c - prev)
    return math.nan


def approx(a, b, eps=1e-9):
    return (a is None and b is None) or (math.isclose(a, b, rel_tol=eps, abs_tol=eps))


def main():
    print("== demo 1: official error-analysis example (220ms spike) ==")
    # practices/histograms: 桶 {0.1,0.2,0.3,0.45,+Inf}, 全部观测落在 (0.2,0.3]
    les = [0.1, 0.2, 0.3, 0.45, math.inf]
    counts = [0, 0, 100, 100, 100]
    p95 = histogram_quantile(0.95, les, counts)
    print(f"    buckets le={{0.1,0.2,0.3,0.45,+Inf}}, 100 obs in (0.2,0.3]")
    print(f"    p95 = {p95:.3f}s (est) vs true 0.220s -> err {abs(p95-0.22)*1000:.0f}ms")
    assert approx(p95, 0.295), "must match doc: 0.2 + 0.1*0.95"

    print("\n== demo 2: spike shifts +100ms -> boundary discontinuity ==")
    counts2 = [0, 0, 0, 100, 100]          # 尖峰移到 (0.3,0.45]
    p95b = histogram_quantile(0.95, les, counts2)
    print(f"    p95 = {p95b:.3f}s (true 0.320s, err {abs(p95b-0.32)*1000:.0f}ms)")
    assert approx(p95b, 0.3 + 0.15 * 0.95)
    print(f"    estimate jumped {p95:.3f} -> {p95b:.3f} though true value only +100ms")

    print("\n== demo 3: full histogram p50/p90/p95/p99 ==")
    # 官方 exposition 示例的桶: 24054/33444/100392/129389/133988/144320
    les3 = [0.05, 0.1, 0.2, 0.5, 1.0, math.inf]
    counts3 = [24054, 33444, 100392, 129389, 133988, 144320]
    for phi in (0.5, 0.9, 0.95, 0.99):
        print(f"    p{int(phi*100):02d} = {histogram_quantile(phi, les3, counts3):.3f}s")
    assert approx(histogram_quantile(0.5, les3, counts3), 0.1 + 0.1 * (0.5*144320-33444)/(100392-33444))
    print(f"    min-est(φ=0) = {histogram_quantile(0, les3, counts3):.3f}s, "
          f"max-est(φ=1) = {histogram_quantile(1, les3, counts3):.3f}s")

    print("\n== demo 4: boundary rules ==")
    assert math.isnan(histogram_quantile(0.5, [0.1], [10]))            # <2 桶
    assert math.isnan(histogram_quantile(0.5, [0.1, 0.3], [5, 10]))    # 缺 +Inf
    assert math.isinf(histogram_quantile(-0.1, les3, counts3))         # φ<0 -> -Inf
    assert math.isinf(histogram_quantile(1.1, les3, counts3))          # φ>1 -> +Inf
    assert math.isnan(histogram_quantile(math.nan, les3, counts3))     # φ=NaN
    print("    <2 buckets / missing +Inf -> NaN; phi out of range -> +/-Inf; NaN phi -> NaN")
    # 最高桶特例: rank 落在 +Inf 桶 -> 返回次高上界 1.0
    hi = histogram_quantile(0.999999, les3, counts3)
    assert approx(hi, 1.0)
    print(f"    quantile in top bucket -> second-highest bound = {hi}")

    print("\n== demo 5: monotonicity repair ==")
    bad = [10, 20, 15, 30, 30]            # 0.2 桶计数比 0.1 桶还小 -> 非法累积
    fixed = repair_monotonic(bad)
    assert fixed == [10, 20, 20, 30, 30]
    print(f"    {bad} -> {fixed} (non-monotonic bucket raised)")
    tiny = [1.0, 1.0 + 1e-16]             # 相对差 < 1e-12: 视为浮点噪声
    assert repair_monotonic([1.0, 1.0 - 1e-16]) == [1.0, 1.0]
    print("    sub-1e-12 relative dip treated as float noise")

    print("\n== demo 6: summary quantiles are NOT aggregatable ==")
    inst_a, inst_b = 0.100, 0.300         # 两实例的预计算 p95
    print(f"    avg(p95_a={inst_a}, p95_b={inst_b}) = {(inst_a+inst_b)/2:.3f}s "
          f"(statistically meaningless, 'BAD!' per docs)")
    # 正确做法: 合并桶后再取分位数
    les_m = [0.1, 0.3, math.inf]
    counts_m = [1, 2, 2]                  # A: 1 obs<=0.1; B: 1 obs in (0.1,0.3]
    merged = histogram_quantile(0.95, les_m, counts_m)
    assert approx(merged, 0.3), "merged-bucket p95 falls in top bucket -> 0.3"
    print(f"    merged-bucket p95 = {merged}s (correct, 'GOOD' per docs)")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
