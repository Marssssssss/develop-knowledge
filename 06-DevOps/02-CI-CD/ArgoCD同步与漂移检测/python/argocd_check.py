"""自检(同步侧): Argo CD 自动同步 / 自愈 / prune / 调和时序 的语义断言。

diff 侧的断言在 ``argocd_check_diff.py``;两个文件共用 ``argocd_harness.py``。
行为与数字均来自 auto_sync 与 diffing 两篇官方文档原文(见 README「参考资料」)。
运行: python argocd_check.py（diff 侧由本文件一并调用）
"""

from __future__ import annotations

from argocd_check_diff import test_diff
from argocd_harness import check, raises, report
from argocd_sync import (
    DEFAULT_RECONCILIATION_S,
    MAX_SYNC_PERIOD_S,
    STATUS_OUT_OF_SYNC,
    STATUS_SYNCED,
    STATUS_UNKNOWN,
    automated_enabled,
    prune_decision,
    reconciliation_period,
    retry_delays,
    rollback_allowed,
    self_heal_recheck_delay,
    sync_decision,
)


def app(**kw):
    base = {"sync_policy": {"automated": {}}, "sync_status": STATUS_OUT_OF_SYNC,
            "revision": "sha1", "param_hash": "p", "last_success": None,
            "last_failed": None, "live_drift": False}
    base.update(kw)
    return base


# ============================ 1. automated 三态 ============================

def test_automated_flag():
    print("[1] syncPolicy.automated 的三态语义")
    check("automated 缺失 -> 未开自动同步", not automated_enabled({}))
    check("automated: {} -> 开启", automated_enabled({"automated": {}}))
    check("enabled 缺失(null) -> 等同开启",
          automated_enabled({"automated": {"prune": True, "selfHeal": True}}))
    check("enabled: true -> 开启", automated_enabled({"automated": {"enabled": True}}))
    check("enabled: false -> 关闭", not automated_enabled({"automated": {"enabled": False}}))
    check("enabled: false 时 prune/selfHeal 写了也不生效",
          not automated_enabled({"automated": {"enabled": False, "prune": True, "selfHeal": True}}))
    check("enabled: false 时不会自动同步",
          sync_decision(app(sync_policy={"automated": {"enabled": False}}))[0] is False)
    check("enabled: None 时会自动同步",
          sync_decision(app(sync_policy={"automated": {"enabled": None}}))[0] is True)


# ============================ 2. 同步决策 ============================

def test_decision():
    print("[2] 自动同步的三个短路条件")
    check("OutOfSync 且无历史 -> 发起同步", sync_decision(app())[0] is True)

    for st in (STATUS_SYNCED, STATUS_UNKNOWN):
        ok, reason = sync_decision(app(sync_status=st))
        check("状态 %s -> 不尝试自动同步" % st, ok is False and "OutOfSync" in reason, reason)

    ok, reason = sync_decision(app(last_success=("sha1", "p")))
    check("同一 (commit,参数) 已成功且未开 selfHeal -> 跳过",
          ok is False and "selfHeal" in reason, reason)
    ok, _ = sync_decision(app(last_success=("sha1", "p"),
                             sync_policy={"automated": {"selfHeal": True}}))
    check("同组合已成功但开了 selfHeal -> 重新同步", ok is True)

    ok, reason = sync_decision(app(last_failed=("sha1", "p")))
    check("同一组合曾失败 -> 不再重试", ok is False and "失败" in reason, reason)
    ok, reason = sync_decision(app(last_failed=("sha1", "p"),
                                  sync_policy={"automated": {"selfHeal": True}}))
    check("失败短路优先于 selfHeal(selfHeal 也救不回来)",
          ok is False and "失败" in reason, reason)

    ok, _ = sync_decision(app(last_success=("other", "p")))
    check("不同 commit -> 正常同步", ok is True)
    ok, _ = sync_decision(app(last_success=("sha1", "other")))
    check("不同参数 -> 正常同步", ok is True)

    ok, reason = sync_decision(app(live_drift=True))
    check("仅 live 漂移且未开 selfHeal -> 跳过", ok is False and "漂移" in reason, reason)
    ok, _ = sync_decision(app(live_drift=True, sync_policy={"automated": {"selfHeal": True}}))
    check("仅 live 漂移但开了 selfHeal -> 自愈", ok is True)

    check("self-heal 默认复查间隔 5 秒", self_heal_recheck_delay() == 5)
    check("self-heal 间隔可配置", self_heal_recheck_delay(30) == 30)
    check("开启自动同步后不能 rollback", rollback_allowed({"automated": {}}) is False)
    check("未开自动同步可 rollback", rollback_allowed({}) is True)
    check("显式关闭 automated 后可以 rollback",
          rollback_allowed({"automated": {"enabled": False}}) is True)


# ============================ 3. prune / allowEmpty ============================

def test_prune():
    print("[3] prune 与 allowEmpty")
    git, live = {"Deployment/a"}, {"Deployment/a", "Service/old"}
    got, reason = prune_decision({}, git, live)
    check("prune 默认关闭: 孤立资源不删除", got == set() and "安全机制" in reason, reason)
    got, reason = prune_decision({"prune": True}, git, live)
    check("prune 开启: 删除孤立资源", got == {"Service/old"}, str(got))
    got, reason = prune_decision({"prune": True}, live, live)
    check("无孤立资源时不删任何东西", got == set(), str(got))
    got, reason = prune_decision({"prune": True}, set(), {"Deployment/a"}, git_is_empty=True)
    check("目标集为空且未开 allowEmpty -> 拒绝清空", got == set() and "allowEmpty" in reason, reason)
    got, _ = prune_decision({"prune": True, "allowEmpty": True}, set(), {"Deployment/a"},
                            git_is_empty=True)
    check("开启 allowEmpty 后允许清空", got == {"Deployment/a"}, str(got))
    got, _ = prune_decision({"prune": False, "allowEmpty": True}, set(), {"Deployment/a"},
                            git_is_empty=True)
    check("allowEmpty 不能绕过 prune 关闭", got == set(), str(got))


# ============================ 4. 调和周期与退避 ============================

def test_timing():
    print("[4] 调和间隔与失败退避")
    check("默认调和周期 = 120 + 60 = 180 秒(恰为官方最大 3 分钟)",
          reconciliation_period() == 180.0 and reconciliation_period() == MAX_SYNC_PERIOD_S)
    check("自定义 timeout.reconciliation",
          reconciliation_period(300, 60) == MAX_SYNC_PERIOD_S)
    check("小值与 jitter 相加", reconciliation_period(30, 10) == 40.0)
    check("默认 reconciliation 常量 = 120", DEFAULT_RECONCILIATION_S == 120)

    pol = {"retry": {"limit": 5, "backoff": {"duration": 5, "factor": 2, "maxDuration": 30}}}
    check("退避序列 5,10,20,30,30(被 maxDuration 封顶)",
          retry_delays(pol, 5) == [5, 10, 20, 30, 30], str(retry_delays(pol, 5)))
    check("limit 截断请求次数", len(retry_delays(pol, 9)) == 5)
    check("limit = -1 表示无限次(此处请求 7 次)",
          len(retry_delays({"retry": {"limit": -1,
                                      "backoff": {"duration": 1, "factor": 1}}}, 7)) == 7)
    check("默认无 retry 配置 -> 不退避", retry_delays({}, 3) == [])
    check("refresh 开关不影响退避序列",
          retry_delays({"retry": {"limit": 2, "refresh": True,
                                  "backoff": {"duration": 1, "factor": 1}}}, 2) == [1, 1])




def main():
    print("Argo CD 语义自检")
    test_automated_flag()
    test_decision()
    test_prune()
    test_timing()
    test_diff()
    report()


if __name__ == "__main__":
    main()
