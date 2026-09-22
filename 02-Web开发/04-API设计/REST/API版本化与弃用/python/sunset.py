"""Deprecation / Sunset 两个响应头的最小可运行模型。

口径来源（本轮实读原文）：
  - draft-ietf-httpapi-deprecation-header-07（22 226 字节）
      * §2.1：Deprecation 是 **Item Structured Header**，取值 `sf-date`；
        `@1688169599` 表示 2023-06-30T23:59:59Z。
      * §2.2：作用域是被响应的那个资源；服务端 MAY 自定义更大作用域，
        但不知情的消费者看不到它。
      * §3：`deprecation` 链接关系类型指向弃用说明；没有 Deprecation 头时
        也可以先挂这个链接（提前公布弃用策略）。
      * §4：Sunset 的时间戳 **MUST NOT** 早于 Deprecation 的时间戳。
      * §5：弃用**不改变**资源行为，已弃用资源 SHOULD 照旧可用。
  - RFC 8594 The Sunset HTTP Header Field（23 588 字节）
      * §3：Sunset 值是 **HTTP-date**，SHOULD 是未来时刻；
        "It is safest to consider timestamps in the past mean the present time"。
      * §1.4：两阶段 —— 第一阶段"不再推荐"**不适合**用 Sunset（此时 API 仍可用）；
        第二阶段"下线"才用。
      * §5：Sunset 只作用于返回它的那个资源，扩大作用域由资源自己定义。
      * §6：`sunset` 链接关系类型指向 sunset 策略 / 即将到来的 sunset / 缓解方案。
      * 与 HTTP 缓存互补而非重叠（§4）。
  - RFC 9651 §3.3.7（74 086 字节）：`sf-date = "@" sf-integer`，
    表示自 1970-01-01T00:00:00Z 起的秒数（可为负，不含闰秒）；
    解析器 MUST 支持 1..9999 年，即 -62135596800 .. 253402214400。
  - RFC 9110 §5.6.7：HTTP-date 三种格式（IMF-fixdate / rfc850 / asctime），
    接收方 MUST 接受全部三种，发送方 MUST 生成 IMF-fixdate。

运行：python sunset.py
"""

import calendar
import re
import time

MIN_SF_DATE = -62135596800
MAX_SF_DATE = 253402214400

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

_IMF = re.compile(
    r"^(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun), (?P<day>\d{2}) "
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(?P<year>\d{4}) (?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2}) GMT$")
_RFC850 = re.compile(
    r"^(?P<dow>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
    r"(?P<day>\d{2})-(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-"
    r"(?P<year>\d{2}) (?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2}) GMT$")
_ASCTIME = re.compile(
    r"^(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(?P<day>[ \d]\d) (?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2}) (?P<year>\d{4})$")


# ----------------------------------------------------------- sf-date

def parse_sf_date(value):
    """解析 RFC 9651 §3.3.7 的 `@` + 整数；非法返回 None。"""
    if value is None:
        return None
    text = value.strip()
    if not text.startswith("@"):
        return None
    rest = text[1:]
    if not re.match(r"^-?\d+$", rest):
        return None
    seconds = int(rest)
    if not (MIN_SF_DATE <= seconds <= MAX_SF_DATE):
        return None
    return seconds


def format_sf_date(seconds):
    return "@%d" % int(seconds)


# --------------------------------------------------------- HTTP-date

def _epoch(year, month, day, hour, minute, second):
    return calendar.timegm((year, month, day, hour, minute, second, 0, 1, 0))


def parse_http_date(value):
    """解析 RFC 9110 §5.6.7 的三种 HTTP-date，返回 epoch 秒；非法返回 None。

    rfc850 的两位年没有规范给的换算规则，本 demo 按 POSIX/COBOL 惯例处理
    （70-99 → 19xx，00-69 → 20xx），并在 README 标注为本 demo 口径。
    """
    if value is None:
        return None
    text = value.strip()
    m = _IMF.match(text)
    if m:
        return _epoch(int(m.group("year")), _MONTHS[m.group("mon")],
                      int(m.group("day")), int(m.group("h")),
                      int(m.group("mi")), int(m.group("s")))
    m = _RFC850.match(text)
    if m:
        two = int(m.group("year"))
        year = 1900 + two if two >= 70 else 2000 + two
        return _epoch(year, _MONTHS[m.group("mon")], int(m.group("day")),
                      int(m.group("h")), int(m.group("mi")), int(m.group("s")))
    m = _ASCTIME.match(text)
    if m:
        return _epoch(int(m.group("year")), _MONTHS[m.group("mon")],
                      int(m.group("day").strip()), int(m.group("h")),
                      int(m.group("mi")), int(m.group("s")))
    return None


def imf_fixdate(seconds):
    """发送方 MUST 生成 IMF-fixdate（RFC 9110 §5.6.7）。"""
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(seconds))


# ------------------------------------------------------- 生命周期模型

class Endpoint(object):
    """一个 API 端点：名字 + Deprecation（sf-date）+ Sunset（HTTP-date）。"""

    def __init__(self, name, deprecation=None, sunset=None,
                 deprecation_link=None, sunset_link=None):
        self.name = name
        self.deprecation = deprecation
        self.sunset = sunset
        self.deprecation_link = deprecation_link
        self.sunset_link = sunset_link

    @classmethod
    def from_headers(cls, name, headers, links=None):
        """从真实响应头构造：Deprecation 走 sf-date，Sunset 走 HTTP-date。"""
        dep = parse_sf_date(headers.get("Deprecation"))
        sun = parse_http_date(headers.get("Sunset"))
        links = links or {}
        return cls(name, dep, sun, links.get("deprecation"), links.get("sunset"))

    def errors(self):
        """§4：Sunset 的时间戳 MUST NOT 早于 Deprecation 的时间戳。"""
        out = []
        if self.deprecation is not None and self.sunset is not None \
                and self.sunset < self.deprecation:
            out.append("%s: Sunset 早于 Deprecation（draft §4）" % self.name)
        return out

    def phase(self, now):
        """三阶段：active → deprecated（仍可用）→ sunset（预期不可响应）。

        §5 / RFC 8594 §3：弃用不改变行为；Sunset 过去的时间戳按"当下"处理。
        """
        if self.deprecation is not None and now >= self.deprecation:
            if self.sunset is not None and now >= self.sunset:
                return "sunset"
            return "deprecated"
        return "active"

    def usable(self, now):
        """§5：已弃用资源 SHOULD 照旧可用，只有过了 sunset 才预期不可用。"""
        return self.phase(now) != "sunset"

    def days_until_sunset(self, now):
        if self.sunset is None:
            return None
        delta = self.sunset - now
        return delta // 86400

    def headers(self):
        out = {}
        if self.deprecation is not None:
            out["Deprecation"] = format_sf_date(self.deprecation)
        if self.sunset is not None:
            out["Sunset"] = imf_fixdate(self.sunset)
        return out

    def hint_vs_status(self, now, observed_status):
        """RFC 8594 §3：Sunset 只是 hint，过了时间点也不保证一定是 4xx。

        返回三者之一："before"（未到点，状态码无约束）/"honored"（已过点且非 2xx）/
        "hint-not-kept"（已过点却仍返回 2xx —— 规范并不认为这是错误）。
        """
        if self.sunset is None or now < self.sunset:
            return "before"
        if 200 <= observed_status < 300:
            return "hint-not-kept"
        return "honored"
