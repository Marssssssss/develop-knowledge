"""自检:Alertmanager 的 matcher / 路由 / 抑制 / 静默与通知节奏。

直接 `python demo.py` 实跑。不依赖第三方包与网络。
数值预期按官方默认值推导(group_wait=30s、group_interval=5m、repeat_interval=4h、
resolve_timeout=5m);浮点一律给容差。
"""

import sys

from alert_matcher import (
    GROUP_BY_ALL, Matcher, MatcherError, fingerprint, group_key,
    labels_match, parse_matcher, parse_matchers,
)
from alert_route import (
    Alert, InhibitRule, Route, Silence, dispatch, effective_repeat_interval,
    equal_labels_hold, inhibited_by, parse_duration, resolve_params,
    silenced_by, walk,
)
from alert_state import (
    FIRING, INACTIVE, PENDING, AlertState, Group, NotificationEngine, alerts_labels,
)

PASS = 0
FAIL = []


def check(label, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append("%s  %s" % (label, detail))


def near(a, b, tol=1e-9):
    return abs(a - b) < tol


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except MatcherError:
        return True


def raises_value(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except ValueError:
        return True


def A(**labels):
    return Alert(labels=dict(labels))


# ---------------------------------------------------------------- A matcher
m = parse_matcher('severity="critical"')
check("A1 解析结果", (m.key, m.op, m.value) == ("severity", "=", "critical"), str(m))
check("A2 等值命中", m.matches({"severity": "critical"}))
check("A3 等值不命中", not m.matches({"severity": "warning"}))
check("A4 不等值命中", Matcher("severity", "!=", "critical").matches({"severity": "warning"}))
check("A5 正则整串锚定(坑)", not Matcher("team", "=~", "payments").matches({"team": "payments-eu"}))
check("A6 正则多值用竖线", Matcher("severity", "=~", "critical|warning").matches({"severity": "warning"}))
check("A7 逗号不是多值分隔符",
      not Matcher("severity", "=~", "critical,warning").matches({"severity": "critical"}))
check("A8 反向正则", Matcher("severity", "!~", "info|debug").matches({"severity": "critical"}))
check("A9 缺失标签使 = 不命中", not m.matches({}))
check("A10 缺失标签使 != 命中(坑)", Matcher("env", "!=", "prod").matches({}))
check("A11 缺失标签使 .* 命中", Matcher("env", "=~", ".*").matches({}))
check("A12 缺失标签使 !~ 不命中", not Matcher("env", "!~", ".*").matches({}))
check("A13 AND 语义", labels_match(parse_matchers(['a="1"', 'b="2"']), {"a": "1", "b": "2"}))
check("A14 AND 语义缺一不可", not labels_match(parse_matchers(['a="1"', 'b="2"']), {"a": "1"}))
check("A15 无引号非法", raises(parse_matcher, "severity=critical"))
check("A16 非法运算符", raises(Matcher, "a", "==", "x"))
for bad in ("severity==critical", 'severity~"x"', 'severity="x', '1bad="x"'):
    check("A17 非法 matcher %r" % bad, raises(parse_matcher, bad))
check("A18 空 matcher 列表恒真", labels_match([], {"anything": "1"}))
f1 = fingerprint({"alertname": "A", "cluster": "c1"})
check("A19 指纹稳定", f1 == fingerprint({"cluster": "c1", "alertname": "A"}))
check("A20 不同标签不同指纹", f1 != fingerprint({"alertname": "A", "cluster": "c2"}))

# ---------------------------------------------------------------- B 分组键
check("B1 空 group_by 全部一组", group_key({"a": "1"}, []) == "")
check("B2 '...' 表示不聚合", group_key({"a": "1"}, [GROUP_BY_ALL]) != group_key({"a": "2"}, [GROUP_BY_ALL]))
check("B3 '...' 与指纹一致", group_key({"a": "1"}, [GROUP_BY_ALL]) == fingerprint({"a": "1"}))
k1 = group_key({"alertname": "A", "cluster": "c1"}, ["alertname", "cluster"])
k2 = group_key({"alertname": "A", "cluster": "c1"}, ["alertname", "cluster"])
k3 = group_key({"alertname": "A", "cluster": "c2"}, ["alertname", "cluster"])
check("B4 同值同组", k1 == k2)
check("B5 异值异组", k1 != k3)
check("B6 组键含标签名", "cluster=c1" in k1, k1)
check("B7 缺标签取空值", group_key({}, ["cluster"]) == "cluster=", group_key({}, ["cluster"]))

# ---------------------------------------------------------------- C 路由树
db = Route(name="db", receiver="database-pager", group_wait=10.0,
           matchers=parse_matchers(['service=~"mysql|cassandra"']))
fe = Route(name="fe", receiver="frontend-pager", group_by=["product", "environment"],
           matchers=parse_matchers(['team="frontend"']))
crit = Route(name="crit", receiver="pager-rotation",
             matchers=parse_matchers(['severity="critical"']), continue_=True)
pay = Route(name="pay", receiver="payments-slack", matchers=parse_matchers(['team="payments"']))
plat = Route(name="plat", receiver="platform-slack",
             matchers=parse_matchers(['team="platform"']))
root = Route(name="root", receiver="default-receiver", group_by=["alertname", "cluster"],
             routes=[db, fe, crit, pay, plat])

check("C1 无子路由命中落到 root", [r for r, _p, _e in dispatch(root, {"team": "x"})] == ["default-receiver"])
check("C2 命中子路由", [r for r, _p, _e in dispatch(root, {"service": "mysql"})] == ["database-pager"])
check("C3 命中子路由时不落 root",
      "default-receiver" not in [r for r, _p, _e in dispatch(root, {"team": "frontend"})])
check("C4 子路由继承 group_by 覆盖",
      dispatch(root, {"team": "frontend"})[0][2]["group_by"] == ["product", "environment"])
check("C5 子路由继承 group_wait 覆盖", near(dispatch(root, {"service": "mysql"})[0][2]["group_wait"], 10.0))
check("C6 未覆盖项继承父值",
      near(dispatch(root, {"team": "frontend"})[0][2]["group_wait"], 30.0)
      and near(dispatch(root, {"team": "frontend"})[0][2]["repeat_interval"], 14400.0))
check("C7 continue=true 继续兄弟匹配",
      [r for r, _p, _e in dispatch(root, {"severity": "critical", "team": "payments"})]
      == ["pager-rotation", "payments-slack"])
check("C8 continue=true 但兄弟不匹配则只命中一个",
      [r for r, _p, _e in dispatch(root, {"severity": "critical", "team": "unknown"})]
      == ["pager-rotation"])
crit.continue_ = False
check("C9 continue=false 阻断兄弟(重复打扰的根因)",
      [r for r, _p, _e in dispatch(root, {"severity": "critical", "team": "payments"})]
      == ["pager-rotation"])
crit.continue_ = True
check("C10 路径记录", dispatch(root, {"team": "frontend"})[0][1] == ("fe",), str(dispatch(root, {"team": "frontend"})[0][1]))
nodes = [p[-1] for p, _r, _e in walk(root)]
check("C11 walk 覆盖全树", sorted(nodes) == sorted(["root", "db", "fe", "crit", "pay", "plat"]), str(nodes))
check("C12 默认值落地", resolve_params(Route())["group_interval"] == 300.0
      and resolve_params(Route())["group_wait"] == 30.0
      and resolve_params(Route())["repeat_interval"] == 14400.0)
check("C13 root 带 matchers 即不再匹配全部",
      dispatch(Route(receiver="r", matchers=parse_matchers(['a="1"'])), {"a": "2"}) == [])
check("C14 时长解析 30s", near(parse_duration("30s"), 30.0))
check("C15 时长解析 5m", near(parse_duration("5m"), 300.0))
check("C16 时长解析 4h", near(parse_duration("4h"), 14400.0))
check("C17 时长解析 100ms", near(parse_duration("100ms"), 0.1))
check("C18 非法时长", raises_value(parse_duration, "5x"))

# ---------------------------------------------------------------- D 抑制
rule = InhibitRule(
    source_matchers=parse_matchers(['severity="critical"']),
    target_matchers=parse_matchers(['severity="warning"']),
    equal=["alertname", "cluster", "service"],
)
crit_alert = A(alertname="LatencyHigh", severity="critical", cluster="c1", service="api")
warn_same = A(alertname="LatencyHigh", severity="warning", cluster="c1", service="api")
warn_other = A(alertname="LatencyHigh", severity="warning", cluster="c1", service="web")
check("D1 同类告警被抑制", len(inhibited_by(warn_same, [crit_alert], [rule])) == 1)
check("D2 equal 不同则不抑制", inhibited_by(warn_other, [crit_alert], [rule]) == [])
check("D3 告警不抑制自己", inhibited_by(crit_alert, [crit_alert], [rule]) == [])
check("D4 源未命中则不抑制",
      inhibited_by(A(alertname="X", severity="warning"), [A(alertname="X", severity="info")], [rule]) == [])
check("D5 target 未命中则不抑制",
      inhibited_by(A(alertname="X", severity="info"), [crit_alert], [rule]) == [])
check("D6 equal 返回规则下标与源指纹",
      inhibited_by(warn_same, [crit_alert], [rule])[0][1] == crit_alert.fingerprint)
check("D7 equal 要求两边都有", equal_labels_hold({"a": "1", "b": "2"}, {"a": "1"}, ["a", "b"]) is False)
check("D8 两边都缺该标签算相等(坑)",
      equal_labels_hold({"a": "1"}, {"a": "1"}, ["b"]) is True)
check("D9 空 equal 恒相等", equal_labels_hold({"a": "1"}, {"b": "2"}, []))
check("D10 空 equal 时同 alertname 的告警互相抑制(经典误配)",
      len(inhibited_by(warn_other, [crit_alert], [InhibitRule(
          parse_matchers(['severity="critical"']), parse_matchers(['severity="warning"']), [])])) == 1)
check("D11 多规则只记一次每组", len(inhibited_by(warn_same, [crit_alert], [rule, rule])) == 2)
check("D12 无活跃源告警则不抑制", inhibited_by(warn_same, [], [rule]) == [])

# ---------------------------------------------------------------- E 静默
sil = Silence(id="s1", matchers=parse_matchers(['alertname="NodeDown"', 'cluster=~"c1|c2"']),
              starts_at=100.0, ends_at=400.0, created_by="alice", comment="维护窗口")
node_c1 = A(alertname="NodeDown", cluster="c1")
node_c3 = A(alertname="NodeDown", cluster="c3")
check("E1 窗口内命中", silenced_by(node_c1, [sil], 200.0) == ["s1"])
check("E2 起始时刻生效(左闭)", silenced_by(node_c1, [sil], 100.0) == ["s1"])
check("E3 结束时刻失效(右开)", silenced_by(node_c1, [sil], 400.0) == [])
check("E4 窗口过期", silenced_by(node_c1, [sil], 500.0) == [])
check("E5 正则未命中", silenced_by(node_c3, [sil], 200.0) == [])
check("E6 告警名不匹配", silenced_by(A(alertname="DiskFull", cluster="c1"), [sil], 200.0) == [])
sil2 = Silence(id="s2", matchers=parse_matchers(['cluster="c1"']), starts_at=0.0, ends_at=1000.0)
check("E7 多静默同时命中", sorted(silenced_by(node_c1, [sil, sil2], 200.0)) == ["s1", "s2"])
check("E8 无静默", silenced_by(node_c1, [], 200.0) == [])

# ---------------------------------------------------------------- F 状态机
st = AlertState()
check("F1 初始 inactive", st.state == INACTIVE)
check("F2 for=0 首次评估即 firing", AlertState().evaluate(0, True, 0.0) == FIRING)
st = AlertState()
check("F3 for=10 首次为 pending", st.evaluate(0, True, 10.0) == PENDING)
check("F4 未满 for 仍 pending", st.evaluate(9, True, 10.0) == PENDING)
check("F5 满 for 转 firing", st.evaluate(10, True, 10.0) == FIRING)
check("F6 active_at 记录进入 pending 的时刻", st.active_at == 0)
check("F7 firing_at 记录转 firing 的时刻", st.firing_at == 10)
check("F8 firing 后继续命中保持", st.evaluate(20, True, 10.0) == FIRING)
st = AlertState()
st.evaluate(0, True, 10.0)
check("F9 中途不命中使计时归零", st.evaluate(5, False, 10.0) == INACTIVE and st.active_at is None)
check("F10 归零后重新计时", st.evaluate(6, True, 10.0) == PENDING)
check("F11 重新计时满 for 才 firing", st.evaluate(16, True, 10.0) == FIRING)
st = AlertState()
st.evaluate(0, True, 0.0)
check("F12 firing 后不命中立即 resolved", st.evaluate(1, False, 0.0) == INACTIVE)
st = AlertState()
st.evaluate(0, True, 0.0)
check("F13 keep_firing_for 窗口内保持 firing", st.evaluate(1, False, 0.0, 30.0) == FIRING)
check("F14 keep_firing_for 期内再次命中则续期", st.evaluate(5, True, 0.0, 30.0) == FIRING)
check("F15 超期后 resolved", st.evaluate(40, False, 0.0, 30.0) == INACTIVE)
stp = AlertState()
stp.evaluate(0, True, 10.0)
check("F16 pending 也视为 active", stp.active and stp.sample_value() == 1)
check("F17 ALERTS 序列标签含 alertstate", alerts_labels("A", stp) == {"alertname": "A", "alertstate": "pending"})
stf = AlertState()
stf.evaluate(0, True, 0.0)
check("F19 firing 时 alertstate=firing", alerts_labels("A", stf) == {"alertname": "A", "alertstate": "firing"})
stq = AlertState()
check("F20 inactive 时序列 stale", alerts_labels("A", stq) is None)
check("F21 评估计数", stf.evaluations == 1)

# ---------------------------------------------------------------- G 通知节奏
eng = NotificationEngine()
check("G1 默认 group_wait", near(eng.group_wait, 30.0))
check("G2 默认 group_interval", near(eng.group_interval, 300.0))
check("G3 默认 repeat_interval", near(eng.repeat_interval, 14400.0))
check("G4 repeat 为 group_interval 倍数时不变", near(eng.effective_repeat, 14400.0))
check("G5 非倍数向上取整", near(effective_repeat_interval(400.0, 300.0), 600.0))
check("G6 恰好整倍数", near(effective_repeat_interval(600.0, 300.0), 600.0))
g = Group(key="g", first_seen=0.0)
fps = {"fp1"}
check("G7 group_wait 前不发", eng.dispatch(g, 0.0, fps) is None)
check("G8 差 1 秒仍不发", eng.dispatch(g, 29.0, fps) is None)
check("G9 group_wait 到点首发", eng.dispatch(g, 30.0, fps) == "first")
check("G10 记录发送时刻", near(g.last_sent, 30.0) and len(g.sends) == 1)
check("G11 group_interval 内不发", eng.dispatch(g, 100.0, fps) is None)
check("G12 无变化时不发", eng.dispatch(g, 330.0, fps) is None)
check("G13 新增告警触发 changed", eng.dispatch(g, 330.0, {"fp1", "fp2"}) == "changed")
check("G14 距上次未满 group_interval 不发", eng.dispatch(g, 400.0, {"fp1", "fp2"}) is None)
check("G15 告警 resolved 也触发 changed", eng.dispatch(g, 630.0, {"fp1"}) == "changed")
check("G16 长时间无变化等 repeat", eng.dispatch(g, 1000.0, {"fp1"}) is None)
check("G17 repeat_interval 到点重发", eng.dispatch(g, 630.0 + 14400.0, {"fp1"}) == "repeat")
check("G18 next_check_at 反映节拍", near(eng.next_check_at(g), 630.0 + 14400.0 + 300.0))
g2 = Group(key="g2", first_seen=0.0)
check("G19 group_wait 内全部 resolved 不发通知(天然防抖)",
      eng.dispatch(g2, 100.0, set()) is None and g2.last_sent is None)
g3 = Group(key="g3", first_seen=0.0)
short = NotificationEngine(repeat_interval=600.0)
short.dispatch(g3, 30.0, {"f"})
check("G20 自定义 repeat 生效", short.dispatch(g3, 630.0, {"f"}) == "repeat")
check("G21 未到自定义 repeat 不发", short.dispatch(g3, 630.0, {"f"}) is None)

# ---------------------------------------------------------------- 收尾
if FAIL:
    print("FAILED %d / %d" % (len(FAIL), len(FAIL) + PASS))
    for line in FAIL:
        print("  - " + line)
    sys.exit(1)
print("ALL PASS  %d assertions" % PASS)
