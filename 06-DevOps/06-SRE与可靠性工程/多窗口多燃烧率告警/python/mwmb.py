"""多窗口多燃烧率(multiwindow multi-burn-rate,MWMB)告警模型。

转写对象都是**实读过的源码**:

1. `slok/sloth` 的 `internal/plugin/slo/core/alert_rules_v1/plugin.go` ——
   `mwmbAlertTpl` 全文:

       (
           max({{quick short}} > ({{quick factor}} * {{budgetRatio}})) without (window)
           and
           max({{quick long}}  > ({{quick factor}} * {{budgetRatio}})) without (window)
       )
       or
       (
           max({{slow short}} > ({{slow factor}} * {{budgetRatio}})) without (window)
           and
           max({{slow long}}  > ({{slow factor}} * {{budgetRatio}})) without (window)
       )

   关键点:①短窗与长窗是 **and**(两个都要破线);②quick 与 slow 之间是 **or**;
   ③阈值是 `burnFactor * errorBudgetRatio`,即"燃烧率 × (1-SLO)"。

2. `internal/alert/windows/google-30d.yaml` / `google-28d.yaml` —— 四档的长短窗。

3. OpenSLO v1 的 `AlertCondition`:`kind: burnrate` 时给 `lookbackWindow`(长窗)
   与 `alertAfter`(默认 0m)—— 这是同一套"长窗定级 + 短窗确认"思想的另一种写法。
"""

# Google 默认值(sloth 的 windows YAML 实读):(预算%, 短窗秒, 长窗秒)
GOOGLE_30D = {
    "page_quick": (2.0, 5 * 60.0, 1 * 3600.0),
    "page_slow": (5.0, 30 * 60.0, 6 * 3600.0),
    "ticket_quick": (10.0, 2 * 3600.0, 1 * 86400.0),
    "ticket_slow": (10.0, 6 * 3600.0, 3 * 86400.0),
}

HOUR = 3600.0
DAY = 86400.0


def burn_factor(error_budget_percent, slo_period_s, long_window_s):
    """与 demo 518 同源的 getBurnRateFactor(这里内联以免跨 demo 依赖)。"""
    hours_required = error_budget_percent * (slo_period_s / HOUR) / 100.0
    return hours_required / (long_window_s / HOUR)


def thresholds(slo, period_s, table=None):
    """四档各自的**错误率阈值** = 燃烧率 × (1 - SLO)。"""
    table = table or GOOGLE_30D
    budget_ratio = 1.0 - slo
    out = {}
    for name, (pct, short, long_) in table.items():
        out[name] = {
            "pct": pct,
            "short_s": short,
            "long_s": long_,
            "burn": burn_factor(pct, period_s, long_),
            "threshold": burn_factor(pct, period_s, long_) * budget_ratio,
        }
    return out


def render_expr(slo, period_s, table=None, metric="slo:sli_error:ratio_rate",
                window_label="sloth_window"):
    """照 mwmbAlertTpl 渲染告警表达式。"""
    t = thresholds(slo, period_s, table)
    budget_ratio = 1.0 - slo
    q, s = t["page_quick"], t["page_slow"]
    return (
        "(\n"
        "    max({m}{{[5m]}} > ({qb:.6g} * {br:.6g})) without ({w})\n"
        "    and\n"
        "    max({m}{{[1h]}} > ({qb:.6g} * {br:.6g})) without ({w})\n"
        ")\n"
        "or\n"
        "(\n"
        "    max({m}{{[30m]}} > ({sb:.6g} * {br:.6g})) without ({w})\n"
        "    and\n"
        "    max({m}{{[6h]}} > ({sb:.6g} * {br:.6g})) without ({w})\n"
        ")"
    ).format(m=metric, w=window_label, br=budget_ratio,
             qb=q["burn"], sb=s["burn"])


# ---------------------------------------------------------------- 判定


def fires(rate_short, rate_long, threshold, op="gt"):
    """单档判定:短窗与长窗都破线才算。模板用的是严格 `>`。"""
    if op == "gt":
        return rate_short > threshold and rate_long > threshold
    if op == "gte":
        return rate_short >= threshold and rate_long >= threshold
    raise ValueError("op must be gt or gte")


def evaluate_both(rates, slo, period_s, table=None):
    """rates = {窗口秒: 该窗口内的错误率}。一次算出 page / ticket 是否告警,
    并给出是哪一档触发的(便于对照)。

    `rates` 必须至少包含四档用到的 5m/30m/2h/6h 与 1h/1d/3d 窗口。
    """
    t = thresholds(slo, period_s, table)
    out = {"page": False, "ticket": False, "hit": None}
    for sev, names in (("page", ("page_quick", "page_slow")),
                       ("ticket", ("ticket_quick", "ticket_slow"))):
        for name in names:
            spec = t[name]
            rs = rates.get(spec["short_s"])
            rl = rates.get(spec["long_s"])
            if rs is None or rl is None:
                continue
            if fires(rs, rl, spec["threshold"]):
                out[sev] = True
                if out["hit"] is None:
                    out["hit"] = name
    return out


# ---------------------------------------------------------------- 时间线工具


def trailing_avg(timeline, t, window_s, step_s):
    """时间线在时刻 t 的窗口均值。timeline 是逐 step 的错误率列表。"""
    lo = t - window_s
    vals = []
    for i, rate in enumerate(timeline):
        tt = i * step_s
        # 区间 (t-window, t];与 Prometheus 的左开右闭一致
        if lo < tt <= t:
            vals.append(rate)
    if not vals:
        # 窗口内没有任何样本:不是"错误率为 0",而是无数据。
        # OpenSLO 为此专门给了 alertWhenNoData 开关,本模型返回 None 表示无法判定。
        return None
    return sum(vals) / len(vals)


def window_rates(timeline, t, windows, step_s):
    """一次求出若干窗口在时刻 t 的错误率。"""
    return {w: trailing_avg(timeline, t, w, step_s) for w in windows}
