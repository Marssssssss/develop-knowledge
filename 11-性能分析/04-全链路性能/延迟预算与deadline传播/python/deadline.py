#!/usr/bin/env python3
"""延迟预算的下发：deadline（时刻）与 timeout（时长）的互换、传播与三种分配策略。

口径来自实读的 gRPC 官方 Deadlines 指南：

- "A deadline is used to specify a point in time past which a client is unwilling
  to wait for a response from a server."  —— deadline 是**时刻**，timeout 是**时长**；
- "By default, gRPC does not set a deadline which means it is possible for a client
  to end up waiting for a response effectively forever." —— 不设就是无限等；
- 客户端超时后失败于 **DEADLINE_EXCEEDED**；服务端在 deadline 过后自动取消，
  报 **CANCELLED**（"a gRPC server deals with this situation by automatically
  cancelling a call (CANCELLED status) once a deadline set by the client has passed"）；
  且服务端应用**自己**得去停止它派生出去的活动；
- 传播的关键一句："Since a deadline is set point in time, propagating it as-is to a
  server can be problematic as the clocks on the two servers might not be synchronized.
  To address this gRPC converts the deadline to a timeout from which the already elapsed
  time is already deducted. This shields your system from any clock skew issues."
  —— 也就是**在线路上用 timeout、在本进程内用 deadline**，且换算时扣掉已耗时；
- 官方时序示例：13:00:00 发起、要求 2s 内完成 → 传给下游的是 timeout=1.5s
  （因为本跳已花掉 0.5s）。

本模块额外给出三种把 SLO 拆成每跳预算的策略（等分 / 按成本加权 / 可压缩空间注水），
这部分**规范里没有**，是本 demo 的定义，README 已标注口径。
"""

from __future__ import annotations

DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
CANCELLED = "CANCELLED"
OK = "OK"


def to_timeout(deadline_abs: float, now: float) -> float:
    """deadline（绝对时刻）→ timeout（剩余时长）。``now`` 是**本进程**的时钟。"""
    return deadline_abs - now


def to_deadline(timeout: float, now: float) -> float:
    """timeout（线路上收到的时长）→ 本进程的 deadline。这一步天然免疫时钟偏移。"""
    return now + timeout


def propagate(deadline_abs: float, now: float, reserve: float = 0.0) -> float:
    """把本进程的 deadline 转成下游的 timeout，**扣掉已耗时**，再留出 ``reserve``。

    ``reserve`` 是本跳给自己收尾预留的时间：留给回包序列化、日志、以及
    "就算下游超时我也要能把 CANCELLED 返回给上游"的余量。
    """
    remaining = to_timeout(deadline_abs, now) - reserve
    return remaining if remaining > 0 else 0.0


def client_status(deadline_abs: float, finish: float) -> str:
    return OK if finish <= deadline_abs else DEADLINE_EXCEEDED


def server_status(deadline_abs: float, now: float) -> str:
    """服务端在 deadline 过后自动取消，状态是 CANCELLED 而不是 DEADLINE_EXCEEDED。"""
    return OK if now <= deadline_abs else CANCELLED


# ---------------- 预算分配 ----------------

def split_equal(total: float, n: int) -> list[float]:
    if n <= 0:
        return []
    return [total / n] * n


def split_by_cost(total: float, costs: list[float]) -> list[float]:
    """按历史成本加权。全零成本退化为等分。"""
    s = sum(costs)
    if s <= 0:
        return split_equal(total, len(costs))
    return [total * c / s for c in costs]


def split_by_headroom(total: float, floors: list[float], caps: list[float]) -> dict:
    """注水法：先给每跳下限（不可压缩部分），再把余量按"可压缩空间"比例分摊，封顶于上限。

    返回 ``{alloc, feasible, slack}``；``Σ floors > total`` 时不可行。
    """
    n = len(floors)
    if n == 0:
        return {"alloc": [], "feasible": True, "slack": total}
    if sum(floors) > total + 1e-9:
        return {"alloc": list(floors), "feasible": False, "slack": total - sum(floors)}
    head = [max(0.0, c - f) for f, c in zip(floors, caps)]
    extra = total - sum(floors)
    hsum = sum(head)
    if hsum <= 0:
        alloc = [min(c, f) for f, c in zip(floors, caps)]
    else:
        alloc = [min(c, f + extra * h / hsum) for f, c, h in zip(floors, caps, head)]
    return {"alloc": alloc, "feasible": True, "slack": total - sum(alloc)}


def consumed(alloc: list[float], actual: list[float]) -> list[dict]:
    """逐跳对账：预算 vs 实际，给出超支额。"""
    return [
        {"budget": a, "actual": b, "over": b - a, "over_pct": (b - a) / a if a > 0 else float("inf")}
        for a, b in zip(alloc, actual)
    ]
