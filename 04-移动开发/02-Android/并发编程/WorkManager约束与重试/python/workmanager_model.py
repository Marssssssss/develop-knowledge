#!/usr/bin/env python3
"""WorkManager 教学模型(Constraints / 退避重试 / WorkRequest / Engine)。

由 workmanager_check.py 拆分而来(仅做行搬运,代码逐字节不变);
自检断言见 workmanager_check.py。

运行: python3 workmanager_check.py
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_BACKOFF_MS = 10 * 1000
MAX_BACKOFF_MS = 5 * 60 * 60 * 1000
DEFAULT_BACKOFF_DELAY_MS = 30 * 1000
EXECUTION_WINDOW_MS = 10 * 60 * 1000

# --------------------------------------------------------------------- Constraints
@dataclass
class Device:
    network: str = "CONNECTED"          # CONNECTED / DISCONNECTED
    metered: bool = False
    roaming: bool = False
    charging: bool = False
    idle: bool = False
    battery_low: bool = False
    storage_low: bool = False


@dataclass
class Constraints:
    network_type: str = "NOT_REQUIRED"  # NOT_REQUIRED / CONNECTED / UNMETERED / NOT_ROAMING / METERED
    charging: bool = False
    device_idle: bool = False
    battery_not_low: bool = False
    storage_not_low: bool = False

    def unmet(self, device: Device) -> list[str]:
        """返回**未满足**的条件名列表;空列表 = 约束已满足(可运行)。"""
        bad: list[str] = []
        t = self.network_type
        if t != "NOT_REQUIRED":
            if device.network != "CONNECTED":
                bad.append("network")
            elif t == "UNMETERED" and device.metered:
                bad.append("network_unmetered")
            elif t == "NOT_ROAMING" and device.roaming:
                bad.append("network_not_roaming")
            elif t == "METERED" and not device.metered:
                bad.append("network_metered")
        if self.charging and not device.charging:
            bad.append("charging")
        if self.device_idle and not device.idle:
            bad.append("device_idle")
        if self.battery_not_low and device.battery_low:
            bad.append("battery_not_low")
        if self.storage_not_low and device.storage_low:
            bad.append("storage_not_low")
        return bad

    def met(self, device: Device) -> bool:
        return not self.unmet(device)


# --------------------------------------------------------------------- 退避
def backoff_delay_ms(policy: str, base_ms: int, attempt: int) -> int:
    """attempt 从 1 起(第 1 次重试)。base 与结果都被 clamp 到官方区间。"""
    base = min(max(base_ms, MIN_BACKOFF_MS), MAX_BACKOFF_MS)
    delay = base * (2 ** (attempt - 1)) if policy == "EXPONENTIAL" else base * attempt
    return min(delay, MAX_BACKOFF_MS)


def within_execution_window(duration_ms: int) -> bool:
    """官方:Worker 最多有 10 分钟完成并返回 Result,之后会被通知停止。"""
    return duration_ms <= EXECUTION_WINDOW_MS


# --------------------------------------------------------------------- 引擎
class WorkRequest:
    def __init__(self, name, worker, constraints: Constraints | None = None,
                 depends_on: str | None = None, policy: str = "EXPONENTIAL",
                 base_ms: int = DEFAULT_BACKOFF_DELAY_MS):
        self.name, self.worker = name, worker
        self.constraints = constraints or Constraints()
        self.depends_on = depends_on
        self.policy, self.base_ms = policy, base_ms
        self.attempts = 0
        self.state = "BLOCKED" if depends_on else "ENQUEUED"
        self.next_retry_at = 0
        self.runs = 0

    def __repr__(self) -> str:
        return f"{self.name}:{self.state}(attempts={self.attempts})"


class Engine:
    def __init__(self, device: Device):
        self.device, self.now = device, 0
        self.requests: list[WorkRequest] = []
        self.deferrals: list[str] = []
        self.trace: list[str] = []

    # --- 入队(含唯一工作策略) ---
    def enqueue(self, req: WorkRequest, unique_name: str | None = None,
                conflict: str = "KEEP") -> WorkRequest:
        if unique_name:
            existing = next((r for r in self.requests
                             if r.name == unique_name and r.state not in ("SUCCEEDED", "FAILED", "CANCELLED")),
                            None)
            if existing is not None:
                if conflict == "KEEP":
                    self.trace.append(f"{req.name} ignored by KEEP")
                    return existing
                self._cancel(existing)
        self.requests.append(req)
        self.trace.append(f"{req.name} enqueued")
        return req

    def _set(self, req: WorkRequest, state: str) -> None:
        req.state = state
        self.trace.append(f"@{self.now} {req.name} -> {state}")

    def _cancel(self, req: WorkRequest) -> None:
        self._set(req, "CANCELLED")
        for r in self.requests:
            if r.depends_on == req.name and r.state not in ("CANCELLED",):
                self._cancel(r)

    def get(self, name: str) -> WorkRequest:
        return next(r for r in self.requests if r.name == name)

    # --- 一次调度回合 ---
    def step(self) -> bool:
        progressed = False
        for req in list(self.requests):
            if req.state == "BLOCKED":
                dep = self.get(req.depends_on)
                if dep.state == "SUCCEEDED":
                    self._set(req, "ENQUEUED")
                    progressed = True
                elif dep.state in ("FAILED", "CANCELLED"):
                    self._cancel(req)
                    progressed = True
            if req.state != "ENQUEUED" or self.now < req.next_retry_at:
                continue
            unmet = req.constraints.unmet(self.device)
            if unmet:
                self.deferrals.append(f"@{self.now} {req.name} held: {','.join(unmet)}")
                continue
            self._set(req, "RUNNING")
            req.runs += 1
            outcome = req.worker(self, req)
            progressed = True
            if outcome == "success":
                self._set(req, "SUCCEEDED")
            elif outcome == "failure":
                self._set(req, "FAILED")          # 依赖方在下一回合被取消
            elif outcome == "constraint_lost":
                req.attempts += 1                 # 约束丢失 → 停止并重新排队
                req.next_retry_at = self.now + backoff_delay_ms(req.policy, req.base_ms, req.attempts)
                self._set(req, "ENQUEUED")
            elif outcome == "retry":
                req.attempts += 1
                req.next_retry_at = self.now + backoff_delay_ms(req.policy, req.base_ms, req.attempts)
                self._set(req, "ENQUEUED")
            else:
                raise ValueError(outcome)
        return progressed

    def run(self, max_steps: int = 20) -> None:
        for _ in range(max_steps):
            if not self.step():
                return

    def advance(self, ms: int) -> None:
        self.now += ms

