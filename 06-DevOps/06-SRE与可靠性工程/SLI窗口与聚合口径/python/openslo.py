"""OpenSLO v1 的 SLO 时间窗口与 ratioMetric 口径模型。

来源:`OpenSLO/OpenSLO` 仓库 `README.md`(v1 规范正文)实读。转写要点:

- **duration-shorthand** 由「正整数 + 单个大小写敏感后缀」构成,允许的后缀是
  m(分)/ h(时)/ d(日)/ w(周)/ M(月)/ Q(季)/ Y(年)。规范原文明确:
  "This specification does not put requirements on how (or whether) to implement
  each postfix, therefore implementers are free to pick an implementation that
  best suits their environments."
  故本模型把 m/h/d/w 当**固定时长**,把 M/Q/Y 当**日历单位**,并在 README 标注该口径。
- **timeWindow** 是"列表但只接受恰好一项",二选一:
  rolling(`isRolling: true`,只给 `duration`)或 calendar-aligned
  (给 `duration` + `calendar.startTime` + `calendar.timeZone`,`isRolling` 缺省 false)。
- **ratioMetric** 三选一形态:`{good, total}`、`{bad, total}` 或 `raw`;
  `raw` 必须配 `rawType: success | failure`,规范原文注释即
  "success – good/total"、"failure – bad/total"。

日历运算一律用 naive datetime 的墙上时间(规范给的 startTime 就是不带时区偏移的
墙上时间,时区另由 IANA 名给出),月份加法做**末尾钳制**(1/31 + 1 月 = 2/28)。
"""

import datetime as _dt

# 固定时长后缀(秒)
_FIXED = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
# 日历单位后缀
_CAL = {"M": 1, "Q": 3, "Y": 12}


class Duration:
    """duration-shorthand。"""

    def __init__(self, text):
        if not text or len(text) < 2:
            raise ValueError("bad duration %r" % text)
        self.text = text
        self.postfix = text[-1]
        self.number = int(text[:-1])
        if self.number <= 0:
            raise ValueError("duration must be positive: %r" % text)
        if self.postfix in _FIXED:
            self.is_calendar = False
            self.seconds = self.number * _FIXED[self.postfix]
            self.months = None
        elif self.postfix in _CAL:
            self.is_calendar = True
            self.months = self.number * _CAL[self.postfix]
            self.seconds = None
        else:
            raise ValueError("unknown postfix %r" % self.postfix)


def _add_months(dt, months):
    """日历月份加法,日号超出目标月长度时钳到月末。"""
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    d = dt.day
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    if d > last:
        d = last
    return dt.replace(year=y, month=m, day=d)


def rolling_window(now, duration):
    """rolling:`(now - duration, now]`。窗口**长度恒定**,且永远贴着 now。"""
    if isinstance(duration, str):
        duration = Duration(duration)
    if duration.is_calendar:
        raise ValueError("rolling window needs a fixed-length duration")
    return (now - duration.seconds, now)


def calendar_window(now, start, duration):
    """calendar-aligned:返回 now 所在的那一个日历窗口 `(lo, hi]`。

    窗口边界是 `start + k 个日历单位`,因此**长度不恒定**:`1M` 在 2 月是 28 天、
    在 7 月是 31 天。这是 rolling 与 calendar 最实质的差别,不是"写法不同"。
    """
    if isinstance(duration, str):
        duration = Duration(duration)
    if not duration.is_calendar:
        raise ValueError("calendar window needs M/Q/Y")
    lo = start
    while True:
        hi = _add_months(lo, duration.months)
        if lo <= now < hi:
            return (lo, hi)
        lo = hi


# ---------------------------------------------------------------- ratioMetric


def sli_from_good(good, total):
    """`{good, total}` 形态:SLI = good / total。"""
    if total == 0:
        return None
    return good / total


def sli_from_bad(bad, total):
    """`{bad, total}` 形态:SLI = 1 - bad / total。"""
    if total == 0:
        return None
    return 1.0 - bad / total


def sli_from_raw(raw, raw_type):
    """`raw` 形态:`rawType` 决定要不要取补。

    success → 存的就是 good/total,直接用;failure → 存的是 bad/total,必须取补。
    把 failure 当 success 用会让 SLI 上下颠倒,是这套规范最容易写错的一处。
    """
    if raw_type == "success":
        return raw
    if raw_type == "failure":
        return 1.0 - raw
    raise ValueError("rawType must be success or failure, got %r" % raw_type)


def parse_start(text):
    """`calendar.startTime` 的格式:`2020-01-21 12:30:00`,24 小时制、不带时区。"""
    return _dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
