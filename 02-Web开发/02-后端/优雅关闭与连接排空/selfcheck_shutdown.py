"""优雅关闭与连接排空 —— 自检。

断言策略：不写「应然」式的复述，而是做**对照实验** ——
同一份连接夹具分别喂给 Shutdown 与 Close，断言两者的**差异集**；
轮询间隔断言**抖动取 0 与取上界两个极端下的精确序列**。
"""

from main import (STATE_ACTIVE, STATE_IDLE, STATE_NEW, Conn, Listener, Server,
                  pod_termination)

PASS = [0]


def ok(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        PASS[0] += 1
        print(f"  PASS  {name}{(' -> ' + extra) if extra else ''}")
    else:
        raise AssertionError(f"FAIL  {name}{(' -> ' + extra) if extra else ''}")


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def fixture():
    """1 条活跃 + 1 条 idle + 1 条「新建即陈旧」(StateNew 且已超 5s)。

    返回 (server, conns 引用表, listener)。
    """
    s = Server()
    ln = Listener("ln0")
    s.track_listener(ln, True)
    conns = {
        "active": Conn("active", STATE_ACTIVE, state_since=-10.0),
        "idle": Conn("idle", STATE_IDLE, state_since=1.0),
        "stale-new": Conn("stale-new", STATE_NEW, state_since=-6.0),
    }
    for c in conns.values():
        s.track_conn(c, True)
    return s, conns, ln


print("== 1. 轮询间隔：抖动为 0 时的精确倍增与 500ms 夹取 ==")
s = Server()
busy = Conn("busy", STATE_ACTIVE, state_since=-10.0)
s.track_conn(busy, True)
res, log = s.shutdown(ctx_deadline_ms=5000.0, jitter=0.0, now_sec=0.0)
intervals = [iv for _, iv in log]
expected = [1, 2, 4, 8, 16, 32, 64, 128, 256] + [500.0] * (len(intervals) - 9)
ok("ctx 到期而非常规结束", res == "ctx", res)
ok("间隔序列 1,2,4,...,256 后夹到 500ms", intervals == expected, str(intervals[:12]))
ok("首次 closeIdleConns 发生在任何等待之前", log[0][0] == 0.0, str(log[0]))
ok("第 k 次轮询时刻等于前 k-1 个间隔之和",
   approx(log[3][0], 1 + 2 + 4) and approx(log[4][0], 1 + 2 + 4 + 8),
   f"{log[3][0]} {log[4][0]}")
ok("活跃连接自始至终未被 Shutdown 关闭", not busy.closed)

print("== 2. 抖动边界：rand.IntN(base/10) 的上界是 +10% ==")
s = Server()
s.track_conn(Conn("busy", STATE_ACTIVE, state_since=-10.0), True)
_, log_max = s.shutdown(ctx_deadline_ms=10.0, jitter=1.0, now_sec=0.0)
ok("抖动取满时首个间隔 = 1.1ms", approx(log_max[0][1], 1.1), str(log_max[0][1]))
ok("抖动取满时第二个间隔 = 2.2ms", approx(log_max[1][1], 2.2), str(log_max[1][1]))
s = Server()
s.track_conn(Conn("busy", STATE_ACTIVE, state_since=-10.0), True)
_, log_min = s.shutdown(ctx_deadline_ms=10.0, jitter=0.0, now_sec=0.0)
ok("抖动取 0 时首个间隔 = 1.0ms", approx(log_min[0][1], 1.0), str(log_min[0][1]))
ok("抖动取满严格大于取 0（+10% 非空转）", log_max[0][1] > log_min[0][1])

print("== 3. Shutdown 的关闭集合 vs Close 的关闭集合（同一夹具对照）==")
s, conns, ln = fixture()
# 活跃连接永不回到 idle 时，Shutdown 会**无限等待**，只能靠 ctx 到期退出 —— 这正是它与 Close 的分野
res, _ = s.shutdown(ctx_deadline_ms=5000.0, jitter=0.0, now_sec=0.0)
ok("活跃连接不退出时 Shutdown 只能由 ctx 到期收场", res == "ctx", res)
ok("Shutdown 只留下活跃连接", set(s.active_conn) == {"active"}, str(sorted(s.active_conn)))
ok("idle 与陈旧 StateNew 被关且标记来源为 Shutdown",
   conns["idle"].closed and conns["stale-new"].closed
   and conns["idle"].closed_by == "Shutdown"
   and conns["stale-new"].closed_by == "Shutdown")
ok("活跃连接未被关闭", not conns["active"].closed)
ok("listener 已关闭且不再 accept", ln.closed and not ln.accept())

s2, conns2, ln2 = fixture()
s2.close(now_sec=0.0)
ok("Close 清空全部连接（含活跃）", s2.active_conn == {}, str(sorted(s2.active_conn)))
ok("Close 关掉的两条标记来源为 Close",
   all(conns2[k].closed_by == "Close" for k in ("active", "idle", "stale-new")))
ok("Shutdown 与 Close 的差异集恰是活跃连接",
   (not conns["active"].closed) and conns2["active"].closed
   and conns["idle"].closed and conns2["idle"].closed)

print("== 4. Issue 22682：StateNew 的 5 秒阈值 ==")
s = Server()
old_new = Conn("old-new", STATE_NEW, state_since=-6.0)
fresh_new = Conn("fresh-new", STATE_NEW, state_since=-1.0)
s.track_conn(old_new, True)
s.track_conn(fresh_new, True)
quiescent = s.close_idle_conns(now_sec=0.0)
ok("陈旧 StateNew 被当作 idle 关掉", old_new.closed and old_new.closed_by == "Shutdown")
ok("新鲜 StateNew 保留，故不 quiescent", (not fresh_new.closed) and quiescent is False)

print("== 5. unixSec == 0 的「刚建好」连接不算 quiescent ==")
s = Server()
brand_new = Conn("brand-new", STATE_IDLE, state_since=0.0, unix_sec=0.0)
s.track_conn(brand_new, True)
ok("即便状态是 StateIdle，unixSec 为 0 仍保留",
   s.close_idle_conns(now_sec=0.0) is False and not brand_new.closed)

print("== 6. keep-alive 与 listener 登记在关机后的行为 ==")
s = Server()
ok("关机前 keep-alive 可用", s.do_keep_alives() is True)
s.in_shutdown = True
ok("关机后 keep-alive 被关", s.do_keep_alives() is False)
s.set_keep_alives_enabled(True)
ok("关机后即便 SetKeepAlivesEnabled(true) 仍不可用（shuttingDown 优先）",
   s.do_keep_alives() is False and s.disable_keep_alives is False)
s = Server()
ok("关机前 trackListener 成功", s.track_listener(Listener("a"), True) is True)
s.in_shutdown = True
ok("关机后 trackListener 返回 False（Serve 据此报 ErrServerClosed）",
   s.track_listener(Listener("b"), True) is False)

print("== 7. onShutdown 钩子与 ctx 已过期时的行为 ==")
s = Server()
s.register_on_shutdown("drain-notify", lambda: None)
s.track_conn(Conn("busy", STATE_ACTIVE, state_since=-10.0), True)
res, log = s.shutdown(ctx_deadline_ms=0.0, jitter=0.0, now_sec=0.0)
ok("ctx 已过期时仍先跑一次 closeIdleConns 才返回 ctx",
   res == "ctx" and len(log) == 1, f"{res} {len(log)}")
ok("onShutdown 钩子已执行且只执行一次", s.hook_ran == ["drain-notify"], str(s.hook_ran))

print("== 8. Kubernetes Pod 终止时间线 ==")
r = pod_termination(grace_period_sec=30.0, prestop_sec=0.0, app_drain_sec=10.0)
ok("排空 10s < 宽限 30s：进程自行退出", r["sigkilled"] is False)
r = pod_termination(grace_period_sec=30.0, prestop_sec=0.0, app_drain_sec=40.0)
ok("排空 40s > 宽限 30s：被 SIGKILL", r["sigkilled"] is True)
r = pod_termination(grace_period_sec=30.0, prestop_sec=32.0, app_drain_sec=1.0)
ok("preStop 32s 超宽限：宽限一次性延期到 34s", approx(r["grace_period"], 34.0),
   str(r["grace_period"]))
ok("preStop 超期后整体不被 SIGKILL（延期覆盖了它）", r["sigkilled"] is False)
r = pod_termination(grace_period_sec=30.0, prestop_sec=5.0, app_drain_sec=10.0)
ev = dict(r["events"])
ok("preStop 5s 时 TERM 在 5s 送达", approx(ev["TERM 送达"], 5.0), str(ev["TERM 送达"]))
ok("排空结束 = TERM + 10s", approx(ev["应用排空结束"], 15.0), str(ev["应用排空结束"]))
ok("宽限未被 preStop 改写（未超期时不延期）", approx(r["grace_period"], 30.0))

print(f"\n全部 {PASS[0]} 项断言通过")
