"""Go net/http Server.Shutdown / Server.Close 的可执行模型 + Kubernetes Pod 终止时间线。

对照的官方事实（golang/go src/net/http/server.go，master 分支）：

* ``Shutdown`` 的流程是 ``inShutdown.Store(true)`` → ``closeListenersLocked`` →
  逐个 ``go f()`` 跑 ``onShutdown`` 钩子 → ``listenerGroup.Wait()`` →
  ``for { if closeIdleConns() { return lnerr }; select { ctx.Done / timer.C } }``。
  注意 **closeIdleConns 在第一次等待之前就被调用了一次**（源码 3424-3436 行：
  先 ``time.NewTimer(nextPollInterval())``，再进 ``for`` 立即 ``closeIdleConns``）。
* 轮询间隔：``pollIntervalBase`` 从 1ms 起，每次 ``interval = base + rand.IntN(base/10)``
  （即最多 +10% 抖动），随后 ``base *= 2`` 并夹到 ``shutdownPollIntervalMax = 500ms``。
* ``closeIdleConns`` 逐条 ``getState()``：
  ``StateNew`` 且 ``unixSec < now-5`` 被当作 idle（Issue 22682）；
  ``unixSec == 0`` 视为「刚建好还没置状态」，**不算 quiescent**；
  非 idle 的连接一律保留并把 ``quiescent`` 置 False。
* ``Close`` 与 ``Shutdown`` 的分野：``Close`` 无差别 ``c.rwc.Close()`` 掉
  ``activeConn`` 里**所有**连接（含 StateActive），``Shutdown`` 只关 idle 的。
* ``doKeepAlives() = !disableKeepAlives && !shuttingDown()`` —— 因此
  Shutdown 一旦开始，keep-alive 就被关掉，即使随后再 ``SetKeepAlivesEnabled(true)`` 也没用。
* ``trackListener`` 在 ``shuttingDown()`` 时返回 False，Serve 据此返回 ``ErrServerClosed``。

Kubernetes 侧（kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/）：
默认 ``terminationGracePeriodSeconds = 30``；先跑 preStop 钩子，
钩子超时后 kubelet 只给 **一次性的 2 秒** 宽限延期；随后发 TERM 给 1 号进程；
EndpointSlice 中的 endpoint 不会立刻摘除，而是标记 terminating 且 ready=false；
宽限期到仍在跑的容器收 SIGKILL。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

STATE_NEW = "StateNew"
STATE_ACTIVE = "StateActive"
STATE_IDLE = "StateIdle"

SHUTDOWN_POLL_INTERVAL_MAX_MS = 500.0
SHUTDOWN_POLL_INTERVAL_BASE_MS = 1.0
STATE_NEW_IDLE_AFTER_SEC = 5          # Issue 22682: 5 秒没读到首个请求头就当 idle
PRESTOP_ONETIME_EXTENSION_SEC = 2.0   # kubelet 给 preStop 的一次性延期
DEFAULT_GRACE_PERIOD_SEC = 30.0


class Conn:
    """一条 *已被 Serve 登记* 的连接（对应 net/http 的 conn）。"""

    def __init__(self, cid: str, state: str = STATE_NEW, state_since: float = 0.0,
                 unix_sec: Optional[float] = None):
        self.cid = cid
        self.state = state
        self.state_since = state_since
        # unixSec 是「进入当前状态的时刻」；0 表示还没来得及置状态
        self.unix_sec = state_since if unix_sec is None else unix_sec
        self.closed = False
        self.closed_by = None      # "Shutdown" / "Close"
        self.closed_at = None

    def get_state(self) -> Tuple[str, float]:
        return self.state, self.unix_sec

    def set_state(self, state: str, now_sec: float) -> None:
        self.state = state
        self.unix_sec = now_sec
        self.state_since = now_sec

    def close(self, by: str, at_ms: float) -> None:
        self.closed = True
        self.closed_by = by
        self.closed_at = at_ms


class Listener:
    def __init__(self, name: str):
        self.name = name
        self.closed = False
        self.accept_calls = 0

    def close(self) -> None:
        self.closed = True

    def accept(self) -> bool:
        """listener 关闭后 accept 会失败（Go 里返回 ErrServerClosed 分支）。"""
        self.accept_calls += 1
        return not self.closed


class Server:
    """net/http.Server 关机相关字段的最小模型（时间单位：毫秒）。"""

    def __init__(self):
        self.listeners: Dict[str, Listener] = {}
        self.active_conn: Dict[str, Conn] = {}
        self.in_shutdown = False
        self.disable_keep_alives = False
        self.on_shutdown: List[Callable[[], None]] = []
        self.listener_group = 0
        self.hook_ran: List[str] = []
        self.poll_log: List[Tuple[float, float]] = []   # (closeIdleConns 的时刻, 间隔)

    # ---- 登记/注销 -------------------------------------------------------
    def track_listener(self, ln: Listener, add: bool) -> bool:
        if add:
            if self.shutting_down():
                return False          # Serve 据此返回 ErrServerClosed
            self.listeners[ln.name] = ln
            self.listener_group += 1
        else:
            self.listeners.pop(ln.name, None)
            self.listener_group -= 1
        return True

    def track_conn(self, c: Conn, add: bool) -> None:
        if add:
            self.active_conn[c.cid] = c
        else:
            self.active_conn.pop(c.cid, None)

    def shutting_down(self) -> bool:
        return self.in_shutdown

    def do_keep_alives(self) -> bool:
        return (not self.disable_keep_alives) and (not self.shutting_down())

    def set_keep_alives_enabled(self, v: bool) -> None:
        self.disable_keep_alives = not v

    def register_on_shutdown(self, name: str, f: Callable[[], None]) -> None:
        self.on_shutdown.append(lambda: (self.hook_ran.append(name), f()))

    # ---- 关闭 -----------------------------------------------------------
    def close_listeners_locked(self) -> Optional[str]:
        err = None
        for ln in self.listeners.values():
            ln.close()
        return err

    def close_idle_conns(self, now_sec: float) -> bool:
        """返回 True 表示已经 quiescent（没有任何活跃连接）。"""
        quiescent = True
        for c in list(self.active_conn.values()):
            st, unix_sec = c.get_state()
            if st == STATE_NEW and unix_sec < now_sec - STATE_NEW_IDLE_AFTER_SEC:
                st = STATE_IDLE
            if st != STATE_IDLE or unix_sec == 0:
                quiescent = False
                continue
            c.close("Shutdown", now_sec * 1000.0)
            del self.active_conn[c.cid]
        return quiescent

    def shutdown(self, ctx_deadline_ms: Optional[float] = None, jitter: float = 0.0,
                 now_sec: float = 0.0, max_polls: int = 200
                 ) -> Tuple[str, List[Tuple[float, float]]]:
        """复刻 Shutdown。jitter ∈ [0,1) 映射 Go 的 rand.IntN(base/10)。

        返回 (结果, poll_log)；结果 ∈ {"ok", "ctx"}。
        """
        self.in_shutdown = True
        lnerr = self.close_listeners_locked()
        for f in self.on_shutdown:
            f()                     # 源码里是 `go f()`，模型里同步执行以便断言顺序
        # listenerGroup.Wait()：模型里 Serve 已随 listener 关闭退出
        base = SHUTDOWN_POLL_INTERVAL_BASE_MS
        self.poll_log = []

        def next_interval() -> float:
            nonlocal base
            interval = base + jitter * (base / 10.0)
            base *= 2.0
            if base > SHUTDOWN_POLL_INTERVAL_MAX_MS:
                base = SHUTDOWN_POLL_INTERVAL_MAX_MS
            return interval

        interval = next_interval()
        waited = 0.0               # 已等待的毫秒数（虚拟时钟）
        for _ in range(max_polls):
            self.poll_log.append((waited, interval))
            if self.close_idle_conns(now_sec + waited / 1000.0):
                return ("ok", self.poll_log)
            if ctx_deadline_ms is not None and waited + interval >= ctx_deadline_ms:
                return ("ctx", self.poll_log)
            waited += interval
            interval = next_interval()
        raise RuntimeError("model bug: shutdown 未在 max_polls 内结束")

    def close(self, now_sec: float = 0.0) -> Optional[str]:
        """复刻 Close：无差别关掉所有连接，不等它们回到 idle。"""
        self.in_shutdown = True
        err = self.close_listeners_locked()
        for c in list(self.active_conn.values()):
            c.close("Close", now_sec * 1000.0)
            del self.active_conn[c.cid]
        return err


# ---------------------------------------------------------------------------
# Kubernetes Pod 终止时间线
# ---------------------------------------------------------------------------
def pod_termination(grace_period_sec: float = DEFAULT_GRACE_PERIOD_SEC,
                    prestop_sec: float = 0.0,
                    app_drain_sec: float = 0.0) -> dict:
    """返回终止时间线上的关键事件（秒，以 kubelet 开始本地关机为 0 点）。

    顺序：preStop 钩子 →（若钩子超期则 +2s 一次性延期）→ TERM → 应用排空 → 宽限到期 SIGKILL。
    """
    t = 0.0
    events = []
    if prestop_sec > 0:
        t += prestop_sec
        events.append(("preStop 结束", t))
        if prestop_sec > grace_period_sec:
            # 钩子跑超时：kubelet 请求一次性 2 秒延期
            grace_period_sec = prestop_sec + PRESTOP_ONETIME_EXTENSION_SEC
            events.append(("一次性延期 +2s", grace_period_sec))
    events.append(("TERM 送达", t))
    drain_end = t + app_drain_sec
    events.append(("应用排空结束", drain_end))
    killed = drain_end > grace_period_sec
    events.append(("SIGKILL" if killed else "进程自行退出", max(drain_end, t)))
    return {
        "grace_period": grace_period_sec,
        "events": events,
        "sigkilled": killed,
        "endpoint_ready_false_at": 0.0,   # EndpointSlice 立刻置 ready=false，但不摘除
    }
