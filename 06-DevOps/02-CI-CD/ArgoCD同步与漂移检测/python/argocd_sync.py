"""Argo CD 自动同步 / 自愈 / prune / 调和时序 语义模型(可执行)。

diff 定制与去噪在配套模块 ``argocd_diff.py``(同一 demo 的两半)。

权威依据:
- argo-cd.readthedocs.io «Automated Sync Policy»(user-guide/auto_sync/)

核心事实(全部有原文支撑):
1. **``automated`` 开关的三态**: ``enabled`` 为 null **等同开启**;显式 ``false`` 时
   ``prune``/``selfHeal``/``allowEmpty`` 可以随意写而不生效。
2. **只在 OutOfSync 时尝试自动同步** —— Synced 或 error 状态都不会触发。
3. **每个 (commit SHA1, 应用参数) 组合只尝试一次**:若最近一次成功同步已经就是同一组合,
   第二次不会再试,**除非 ``selfHeal: true``**;而若上一轮对同一组合**失败**过,则不再重试。
4. ``selfHeal: true`` 时,同步会在 **self-heal timeout(默认 5 秒)** 之后再次尝试,该值由
   ``argocd-application-controller`` 的 ``--self-heal-timeout-seconds`` 控制。
5. **开启自动同步的应用不能做 rollback**。
6. 自动同步间隔由 ``argocd-cm`` 的 ``timeout.reconciliation`` 决定,**默认 120s,外加 60s
   抖动,最大周期 3 分钟**。
7. ``prune`` 默认**关闭**(安全机制):Git 里已删除的资源不会自动删除;``allowEmpty`` 再叠加
   一层保护 —— 目标资源集为空时不允许清空应用。
"""

from __future__ import annotations

DEFAULT_SELF_HEAL_TIMEOUT_S = 5
DEFAULT_RECONCILIATION_S = 120
DEFAULT_JITTER_S = 60
MAX_SYNC_PERIOD_S = 180          # 官方: maximum period of 3 minutes

STATUS_SYNCED = "Synced"
STATUS_OUT_OF_SYNC = "OutOfSync"
STATUS_UNKNOWN = "Unknown"


class ArgoError(ValueError):
    """应用/配置层面的校验错误。"""


# ------------------------------------------------------------------ 同步决策

def automated_enabled(sync_policy: dict) -> bool:
    """``syncPolicy.automated.enabled`` 的三态语义: 缺失或 null 都视为开启。"""
    auto = sync_policy.get("automated")
    if auto is None:
        return False                      # 整个 automated 块缺失 = 未开自动同步
    if not isinstance(auto, dict):
        return True
    flag = auto.get("enabled", None)
    return flag is None or flag is True   # null 等同开启, 只有显式 false 才关


def sync_decision(state: dict) -> tuple:
    """返回 ``(是否发起同步, 原因)``。

    ``state`` 需要: ``sync_policy``、``sync_status``、``revision``、``param_hash``、
    ``last_success``(最近成功同步的 (revision, param_hash))、``last_failed``(最近一次
    尝试失败的组合)、``live_drift``(live 与 git 不一致但 Git 未变更)。
    """
    sp = state.get("sync_policy", {})
    if not automated_enabled(sp):
        return False, "automated 未开启(或显式 false)"
    if state["sync_status"] in (STATUS_SYNCED, STATUS_UNKNOWN):
        return False, "只有 OutOfSync 才尝试自动同步"

    key = (state["revision"], state["param_hash"])
    auto = sp.get("automated") or {}
    self_heal = bool(auto.get("selfHeal", False))

    if state.get("last_failed") == key:
        return False, "上一轮对同一 (commit, 参数) 已失败 -> 不再重试"
    if state.get("last_success") == key and not self_heal:
        return False, "同一 (commit, 参数) 已成功同步过, 且未开 selfHeal"
    if state.get("live_drift") and not self_heal:
        return False, "仅 live 漂移(Git 未变更)且未开 selfHeal"
    return True, "OutOfSync 且不命中三条短路"


def self_heal_recheck_delay(self_heal_timeout_s: float = DEFAULT_SELF_HEAL_TIMEOUT_S) -> float:
    return self_heal_timeout_s


def prune_decision(auto: dict, git_resources: set, live_resources: set, git_is_empty=False):
    """返回 ``(要删除的资源集合, 原因)``。"""
    orphans = sorted(live_resources - git_resources)
    if not orphans:
        return set(), "无孤立资源"
    if not auto.get("prune", False):
        return set(), "prune 未开启(默认安全机制): 只标记不删除"
    if git_is_empty and not auto.get("allowEmpty", False):
        return set(), "目标资源集为空且未开 allowEmpty -> 拒绝清空应用"
    return set(orphans), "prune 已开启, 删除 %d 个孤立资源" % len(orphans)


def reconciliation_period(configured_s: float = DEFAULT_RECONCILIATION_S,
                          jitter_s: float = DEFAULT_JITTER_S) -> float:
    return min(configured_s + jitter_s, MAX_SYNC_PERIOD_S)


def rollback_allowed(sync_policy: dict) -> bool:
    return not automated_enabled(sync_policy)


# ------------------------------------------------------------------ 重试退避

def retry_delays(policy: dict, attempts: int) -> list:
    """按 ``limit`` / ``backoff.duration`` / ``factor`` / ``maxDuration`` 展开退避序列。"""
    retry = policy.get("retry") or {}
    limit = retry.get("limit", 0)
    b = retry.get("backoff") or {}
    base = b.get("duration", 0)
    factor = b.get("factor", 1)
    cap = b.get("maxDuration", float("inf"))
    n = attempts if limit == -1 else min(attempts, max(0, limit))
    out, cur = [], base
    for _ in range(n):
        out.append(min(cur, cap))
        cur *= factor
    return out


