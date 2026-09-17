#!/usr/bin/env python3
"""Android Service 生命周期模型(三种形态 / stopSelf(startId) / 重启计划)。

由 service_state_check.py 拆分而来(仅做行搬运,代码逐字节不变);
自检断言见 service_state_check.py。

运行: python3 service_state_check.py
"""

from __future__ import annotations

START_STICKY_COMPATIBILITY, START_STICKY, START_NOT_STICKY, START_REDELIVER_INTENT = 0, 1, 2, 3
BIND_AUTO_CREATE = 0x0001
MAIN_THREAD = "main"


class SecurityException(RuntimeError):
    pass


class Service:
    """极简 Service 生命周期模型;子类覆盖 on_start_command 表达业务逻辑。"""

    def __init__(self, name: str = "MyService", default_result: int = START_STICKY):
        self.name = name
        self.default_result = default_result
        self.events: list[str] = []
        self.queue: list[tuple[int, str]] = []
        self.created = self.started = self.destroyed = False
        self.connections = 0
        self.foreground = False
        self.notification_id: int | None = None
        self.foreground_permission = False
        self.issued_start_id = 0
        self.delivered_start_id = 0
        self.return_code: int | None = None

    # ---------------- 启动
    def enqueue_start(self, intent: str) -> int:
        """startService():多次调用不嵌套,但每次都会产生一次 onStartCommand。"""
        self.create()
        self.started = True
        self.issued_start_id += 1
        self.queue.append((self.issued_start_id, intent))
        return self.issued_start_id

    def dispatch(self) -> None:
        """把队首的 start intent 投递给 onStartCommand(主线程逐个处理,一次一个)。"""
        if not self.queue or self.destroyed:
            return
        start_id, intent = self.queue.pop(0)
        self.delivered_start_id = start_id
        self.events.append(f"onStartCommand(startId={start_id}, intent={intent})")
        self.return_code = self.on_start_command(intent, start_id)

    def on_start_command(self, intent: str, start_id: int) -> int:
        return self.default_result

    def create(self) -> None:
        if not self.created:
            self.created = True
            self.events.append("onCreate")

    # ---------------- 停止
    def stop_service(self) -> None:
        self.started = False
        self._maybe_destroy()

    def stop_self(self, start_id: int | None = None) -> None:
        """仅在 startId 为「最近一次 start」时生效,否则是 no-op(官方 javadoc)。"""
        if start_id is None or start_id == self.issued_start_id:
            self.started = False
            self._maybe_destroy()
        else:
            self.events.append(f"stopSelf({start_id}) ignored: last start is {self.issued_start_id}")

    def _maybe_destroy(self) -> None:
        if not self.started and self.connections == 0 and not self.destroyed:
            if self.foreground:
                self.foreground = False
                self.events.append(f"cancelNotification(id={self.notification_id})")
            self.destroyed = True
            self.created = False
            self.events.append("onDestroy")

    # ---------------- 绑定
    def bind(self, client: str, flags: int = BIND_AUTO_CREATE) -> str:
        self.create()
        self.connections += 1
        if self.connections == 1:
            self.events.append(f"onBind(client={client}, autoCreate={bool(flags & BIND_AUTO_CREATE)})")
        return f"{self.name}.Binder"

    def unbind(self, client: str) -> None:
        self.connections -= 1
        if self.connections == 0:
            self.events.append(f"onUnbind(client={client})")
            self._maybe_destroy()

    # ---------------- 前台
    def start_foreground(self, notification_id: int, api_level: int = 28) -> None:
        if api_level >= 28 and not self.foreground_permission:
            raise SecurityException("requires android.permission.FOREGROUND_SERVICE")
        self.foreground = True
        self.notification_id = notification_id
        self.events.append(f"startForeground(id={notification_id})")

    def set_foreground(self, value: bool) -> None:
        self.events.append("setForeground: ignoring old API call")   # 官方实现即 no-op

    # ---------------- 主线程
    def run_blocking(self, what: str) -> None:
        self.events.append(f"{what} on {MAIN_THREAD}")

    def start_ids(self) -> list[int]:
        return [int(e.split("startId=")[1].split(",")[0]) for e in self.events
                if e.startswith("onStartCommand")]


class StopWithDeliveredId(Service):
    """每次 onStartCommand 都用**自己这次收到的** startId 请求停止。"""

    def on_start_command(self, intent: str, start_id: int) -> int:
        self.stop_self(start_id)
        return START_NOT_STICKY


class StopWithLatestId(Service):
    """乱序反而先调用最近 ID(官方 javadoc 明确警告的情形)。"""

    def on_start_command(self, intent: str, start_id: int) -> int:
        self.stop_self(self.issued_start_id)
        return START_NOT_STICKY


def restart_plan(return_code: int, has_pending_starts: bool) -> dict:
    """按 javadoc 三档语义给出进程被杀后的重建计划。"""
    if return_code == START_NOT_STICKY:
        return {"restart": False, "redeliver": False, "null_intent": False}
    if return_code == START_REDELIVER_INTENT:
        return {"restart": True, "redeliver": has_pending_starts, "null_intent": False}
    if return_code == START_STICKY:
        return {"restart": True, "redeliver": False, "null_intent": not has_pending_starts}
    return {"restart": True, "redeliver": False, "null_intent": False}   # COMPATIBILITY

