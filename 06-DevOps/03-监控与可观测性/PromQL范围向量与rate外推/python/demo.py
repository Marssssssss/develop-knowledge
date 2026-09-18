"""PromQL 范围向量语义与 rate/increase 外推 —— 可实跑自检。

运行：``python demo.py``（纯标准库，退出码 0 表示全部断言通过）

覆盖 6 组语义：
  A 选择器合法性      B 空值匹配与「同标签多 matcher」
  C 正则完全锚定      D lookback 与 staleness
  E 范围向量左开右闭  F offset / @ / 子查询 / 外推与计数器重置
"""

import sys

from promql_engine import (Engine, PromQLError, extrapolated_rate, parse,
                           parse_duration)
from promql_store import MemStore, Sample

PASS = 0
FAIL = []


def check(label, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append("%s | %s" % (label, detail))
        print("FAIL  %s | %s" % (label, detail))


def approx(a, b, tol=1e-9):
    return a is not None and abs(a - b) < tol


# ---------------------------------------------------------------- A 选择器合法性
def group_a():
    # 官方规则：选择器必须给出指标名，或至少一个**不匹配空值**的 matcher。
    # 注意 `job!="x"` 匹配空值（"" != "x" 为真）故非法；而 `job!=""` 不匹配空值故合法。
    for bad in ('{job=~".*"}', '{job!="x"}', '{env=~""}', '{__name__=~".*"}',
                '{a=~".*",b=~".*"}'):
        try:
            parse(bad)
            check("A 非法选择器应报错", False, bad)
        except PromQLError:
            check("A 非法选择器应报错", True)
    for good in ('{job=~".+"}', '{job="x"}', '{job!=""}', '{__name__=~".+"}',
                 '{job=~".*",method="get"}', 'up'):
        parse(good)
        check("A 合法选择器应通过", True, good)
    check("A 无 matcher 的裸花括号非法", _raises(lambda: parse("{}")), "{}")


def _raises(fn):
    try:
        fn()
        return False
    except PromQLError:
        return True


# ------------------------------------------------------- B 空值匹配 / 多 matcher
def group_b():
    st = MemStore()
    st.add("http_requests_total", {}, 0, 1)
    st.add("http_requests_total", {"replica": "rep-a"}, 0, 1)
    st.add("http_requests_total", {"replica": "rep-b"}, 0, 1)
    st.add("http_requests_total", {"environment": "development"}, 0, 1)

    got = st.select("http_requests_total", parse('http_requests_total{environment=""}'
                                                 ).matchers, 10)
    check("B 匹配空值 = 无该标签也命中", len(got) == 3, len(got))
    check("B 有 environment 标签的被排除",
          all("environment" not in lb for lb, _ in got), got)

    got = st.select("http_requests_total",
                    parse('http_requests_total{replica!="rep-a",replica=~"rep.*"}'
                          ).matchers, 10)
    check("B 同标签多 matcher 全通过才命中", len(got) == 1, len(got))
    check("B 同标签多 matcher 命中 rep-b",
          got and got[0][0].get("replica") == "rep-b", got)


# ------------------------------------------------------------ C 正则完全锚定
def group_c():
    st = MemStore()
    for r in ("rep-a", "rep-b", "replication"):
        st.add("up", {"replica": r}, 0, 1)
    check("C =~ 完全锚定（rep 不匹配 replication）",
          len(st.select("up", parse('up{replica=~"rep"}').matchers, 10)) == 0)
    check("C =~ 加 .* 才匹配前缀",
          len(st.select("up", parse('up{replica=~"rep.*"}').matchers, 10)) == 3)
    check("C =~ ^rep$ 与 rep 等价（锚定口径）",
          len(st.select("up", parse('up{replica=~"^rep$"}').matchers, 10)) == 0)


# ------------------------------------------------------- D lookback / staleness
def group_d():
    st = MemStore(lookback=300.0)
    st.add("m", {"a": "1"}, 1000, 7)
    check("D 距求值恰 300s（=lookback）不返回",
          st.select("m", parse("m").matchers, 1300) == [])
    check("D 距求值 299s 返回",
          st.select("m", parse("m").matchers, 1299) == [({"a": "1"}, 7)])
    check("D 取 at-or-before 的最新样本（未来样本不可见）",
          st.select("m", parse("m").matchers, 1000) == [({"a": "1"}, 7)])
    st.mark_stale("m", {"a": "1"}, 1500)
    check("D stale 标记后无值", st.select("m", parse("m").matchers, 1600) == [])
    st.add("m", {"a": "1"}, 1700, 9)
    check("D stale 之后写入新样本即恢复",
          st.select("m", parse("m").matchers, 1750) == [({"a": "1"}, 9)])


# ------------------------------------------------------- E 范围向量左开右闭
def group_e():
    st = MemStore()
    for t in (0, 60, 120, 180, 240, 300):
        st.add("c", {"a": "1"}, t, t)
    eng = Engine(st)
    mx = eng.instant("c[5m]", 300)
    pts = mx[0][1]
    check("E 左边界样本被排除（t=0 不在 5m 窗口内）", all(p.t != 0 for p in pts),
          [p.t for p in pts])
    check("E 右边界样本被包含（t=300 在窗口内）", any(p.t == 300 for p in pts),
          [p.t for p in pts])
    check("E 窗口共 5 个样本（60/120/180/240/300）", len(pts) == 5,
          [p.t for p in pts])
    check("E 时长解析 5m=300s / 1h30m=5400s / 250ms=0.25s",
          parse_duration("5m") == 300 and parse_duration("1h30m") == 5400
          and approx(parse_duration("250ms"), 0.25))


# ------------------------------------------- F offset / @ / 子查询 / 外推
def counter_store():
    """counter cnt{case=...} 的 v = 0.1*t（每秒涨 0.1，即每 60s 涨 6）。"""
    st = MemStore()
    for case, ts in (("A", (60, 120, 180, 240, 300)),
                     ("B", (60, 120, 180, 240)),
                     ("C", (60, 120, 180)),
                     ("one", (300,))):
        for t in ts:
            st.add("cnt", {"case": case}, t, 0.1 * t)

    # 值不再是 0.1*t 的三个场景：计数器重置 / 零点截断 / 阈值平局
    st2 = MemStore()
    for t, v in ((60, 5), (120, 10), (180, 2), (240, 7)):
        st2.add("rst", {"case": "reset"}, t, v)
    for t, v in ((90, 5), (150, 10), (210, 15), (270, 25)):
        st2.add("zro", {"case": "zero"}, t, v)
    for t in (54, 114, 174, 234):
        st2.add("tie2", {"case": "tie"}, t, 0.1 * t)
    return st, st2


def group_f():
    st = MemStore()
    for t in (0, 60, 300, 600, 700):
        st.add("x", {}, t, float(t))
    eng = Engine(st, eval_interval=60.0)

    check("F offset 5m 等价于在 eval-300 求值",
          eng.instant("x offset 5m", 700) == eng.instant("x", 400))
    check("F @ 与 offset 顺序无关（after @）",
          eng.instant("x @ 700 offset 5m", 99999)
          == eng.instant("x offset 5m @ 700", 0))
    check("F 负 offset 可看向求值时刻之后（look ahead）",
          eng.instant("x offset -5m", 0) == [({}, 300.0)],
          eng.instant("x offset -5m", 0))
    check("F offset 必须紧跟选择器（rate(...) offset 5m 非法）",
          _raises(lambda: parse("rate(x[5m]) offset 5m")))
    parse("rate(x[5m] offset 5m)")
    check("F offset 写在范围选择器之后合法", True)

    for t in (0, 60, 120, 180, 240, 300, 600):
        st.add("s", {}, t, float(t))
    check("F @ start()/end() 在区间查询里解析为区间起止",
          eng.instant("s @ start()", 0, (300.0, 600.0)) == [({}, 300.0)]
          and eng.instant("s @ end()", 0, (300.0, 600.0)) == [({}, 600.0)])


def group_g():
    st, st2 = counter_store()
    eng = Engine(st, eval_interval=60.0)
    e2 = Engine(st2, eval_interval=60.0)

    def rate(name, case):
        r = eng.instant('rate(%s{case="%s"}[5m])' % (name, case), 300)
        return r[0][1] if r else None

    check("G 均匀抓取且末样本落在右边界 → rate 精确",
          approx(rate("cnt", "A"), 0.1), rate("cnt", "A"))
    check("G 末样本距右边界 60s（< 阈值 66s）→ 仍精确",
          approx(rate("cnt", "B"), 0.1), rate("cnt", "B"))
    check("G 末样本距右边界 120s（≥ 阈值）→ 只外推 avg/2，低估 30%",
          approx(rate("cnt", "C"), 0.07), rate("cnt", "C"))

    # 阈值平局：avg=60 → 阈值 66；末样本恰在 234 使 durationToEnd == 66
    pts = [Sample(t, 0.1 * t) for t in (54, 114, 174, 234)]
    tie = extrapolated_rate(pts, 0.0, 300.0, True, True)
    strict = 18 * (300.0 / 180.0) / 300.0
    check("G 判据是 >= 阈值（恰等于阈值即降级为 avg/2）",
          approx(tie, 18 * (264.0 / 180.0) / 300.0) and not approx(tie, strict),
          (tie, strict))

    rst = e2.instant('increase(rst{case="reset"}[5m])', 300)
    check("G 计数器重置被补偿：raw = (7-5)+10 = 12",
          approx(rst[0][1], 12 * 300.0 / 180.0), rst)
    naive = e2.instant('increase(rst{case="reset"}[5m])', 300)[0][1]
    check("G 不补偿时的 last-first 只有 2/12（差 6 倍）",
          approx(naive / (2 * 300.0 / 180.0), 6.0))
    check("G resets() 正确计数 1 次",
          e2.instant('resets(rst{case="reset"}[5m])', 300) ==
          [({"case": "reset"}, 1)])

    zro = e2.instant('increase(zro{case="zero"}[5m])', 300)[0][1]
    pts_z = [Sample(t, v) for t, v in ((90, 5), (150, 10), (210, 15), (270, 25))]
    order_a = extrapolated_rate(pts_z, 0.0, 300.0, True, True, "threshold_first")
    order_b = extrapolated_rate(pts_z, 0.0, 300.0, True, True, "zero_first")
    check("G 计数器零点截断改变外推起点：dStart 150→17.14",
          approx(order_a * 300, 20 * 240.0 / 180.0), zro)
    check("G 两种分支顺序在此例给出不同结果（dStart 30 vs 45）",
          not approx(order_a, order_b), (order_a, order_b))

    one = eng.instant('rate(cnt{case="one"}[5m])', 300)
    check("G 窗口内只有 1 个样本 → 无值（len<2 丢弃）", one == [], one)

    r = eng.instant('rate(cnt{case="A"}[5m])', 300)[0][1]
    i = eng.instant('increase(cnt{case="A"}[5m])', 300)[0][1]
    check("G increase = rate × 范围秒数（官方称语法糖）",
          approx(i, r * 300.0), (i, r * 300.0))

    mx = eng.instant("cnt{case=\"A\"}[5m]", 300)
    check("G 裸范围向量求值返回矩阵（列表样本）", len(mx[0][1]) == 5, mx)


# --------------------------------------------------------------- H 子查询
def group_h():
    st = MemStore()
    for t in range(0, 601, 10):
        st.add("q", {"job": "api"}, t, 0.1 * t)
    eng = Engine(st, eval_interval=60.0)
    check("H 子查询默认 resolution = 全局求值间隔（60s → 11 步）",
          len(eng.instant("q[10m:60s]", 600)[0][1]) == 11)
    check("H 子查询显式 30s → 21 步",
          len(eng.instant("q[10m:30s]", 600)[0][1]) == 21)
    inner = eng.instant('rate(q{job="api"}[1m])[10m:60s]', 600)
    vals = [v for _, v in inner[0][1]]
    check("H 子查询内层求 rate：每步都精确 0.1",
          len(vals) == 10 and all(approx(v, 0.1) for v in vals), vals[:3])
    check("H 首步 t=0 窗口内只有 1 个样本 → 该步无值（故 11 步得 10 点）",
          len(eng.instant("q[10m:60s]", 600)[0][1]) == 11)
    check("H resolution 必须为正", _raises(lambda: eng.instant("q[10m:0s]", 600)))


def main():
    for g in (group_a, group_b, group_c, group_d, group_e,
              group_f, group_g, group_h):
        g()
    print("-" * 60)
    if FAIL:
        print("断言失败 %d 项 / 通过 %d 项" % (len(FAIL), PASS))
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部 %d 项断言通过" % PASS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
