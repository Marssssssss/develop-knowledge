"""错误预算与 burn rate 的模型。

两段来源,都是**实读过的原文**:

1. `slok/sloth` 的 `internal/alert/window.go` —— `getBurnRateFactor` 逐行转写:
     hoursRequiredConsumption = errorBudgetPercent * totalWindow.Hours() / 100
     speed = hoursRequiredConsumption / consumptionWindow.Hours()
   源码注释原文: "Error budget speeds based on a full time window, however once we
   have the factor (speed) the value can be used with any time window."
   同文件还记录了 Google 在 SRE workbook 里给的默认值:
     "Page quick: 2% / Page slow: 5% / Ticket quick: 10% / Ticket slow: 10%"
2. OpenSLO v1 的 `budgetingMethod` 三态(Occurrences / Timeslices / RatioTimeslices)。

术语统一:
- error budget(错误预算) = 1 - SLO,即允许"不可靠"的比例
- burn rate(燃烧率) = 实际错误率 / 错误预算率;1 表示"恰好按 SLO 的速度花预算"
"""

HOUR = 3600.0
DAY = 24.0 * HOUR


# ---------------------------------------------------------------- 基础算术


def error_budget(slo):
    """错误预算 = 1 - SLO。SLO 以 [0,1) 的小数给出。"""
    if not 0.0 <= slo < 1.0:
        raise ValueError("SLO must be in [0, 1), got %r" % slo)
    return 1.0 - slo


def budget_minutes(slo, period_s):
    """把错误预算换算成"允许不可靠的分钟数"。99.9% / 30 天 = 43.2 分钟。"""
    return error_budget(slo) * period_s / 60.0


def burn_rate(error_rate, slo):
    """burn rate = 实际错误率 / 错误预算率。"""
    return error_rate / error_budget(slo)


def budget_consumed(error_rate, slo, window_s, period_s):
    """窗口内消耗的预算**占整期预算的比例**。

    严格推导:窗口内的错误事件数 ≈ error_rate × window;整期预算(换算成"错误
    事件当量")= (1-SLO) × period。两者相除,SLO 项被约掉一半后即为下式。
    """
    return error_rate * window_s / (error_budget(slo) * period_s)


def time_to_exhaustion(burn, period_s):
    """按当前燃烧率,把整期预算烧光需要多久 = period / burn。"""
    if burn <= 0:
        return float("inf")
    return period_s / burn


# ---------------------------------------------------------------- Sloth 的因子


def burn_rate_factor(error_budget_percent, slo_period_s, consumption_window_s):
    """照 `getBurnRateFactor` 转写:要在 consumption_window 内花掉 pct% 预算,
    需要的燃烧率(源码里叫 speed)。

        hoursRequiredConsumption = pct * totalHours / 100
        speed = hoursRequiredConsumption / consumptionHours
    """
    if consumption_window_s <= 0:
        raise ValueError("consumption window must be positive")
    hours_required = error_budget_percent * (slo_period_s / HOUR) / 100.0
    return hours_required / (consumption_window_s / HOUR)


class Window(object):
    """sloth 的 `alert.Window`:一个"档位" = 预算百分比 + 短窗 + 长窗。"""

    def __init__(self, error_budget_percent, short_window_s, long_window_s):
        self.error_budget_percent = error_budget_percent
        self.short_window_s = short_window_s
        self.long_window_s = long_window_s

    def validate(self):
        # 源码 Validate():三个字段任一为 0 就报错
        if self.long_window_s == 0:
            raise ValueError("long window is required")
        if self.short_window_s == 0:
            raise ValueError("short window is required")
        if self.error_budget_percent == 0:
            raise ValueError("error budget is required")
        return True

    def speed(self, slo_period_s):
        self.validate()
        return burn_rate_factor(self.error_budget_percent, slo_period_s,
                                self.long_window_s)


class Windows(object):
    """sloth 的 `alert.Windows`:page/ticket × quick/slow 四档。"""

    def __init__(self, slo_period_s, page_quick, page_slow,
                 ticket_quick, ticket_slow):
        self.slo_period_s = slo_period_s
        self.page_quick = page_quick
        self.page_slow = page_slow
        self.ticket_quick = ticket_quick
        self.ticket_slow = ticket_slow

    def validate(self):
        if self.slo_period_s == 0:
            raise ValueError("slo period is required")
        for name in ("page_quick", "page_slow", "ticket_quick", "ticket_slow"):
            getattr(self, name).validate()
        return True

    def speeds(self):
        self.validate()
        return {
            "page_quick": self.page_quick.speed(self.slo_period_s),
            "page_slow": self.page_slow.speed(self.slo_period_s),
            "ticket_quick": self.ticket_quick.speed(self.slo_period_s),
            "ticket_slow": self.ticket_slow.speed(self.slo_period_s),
        }
