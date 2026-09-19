# -*- coding: utf-8 -*-
"""hpa_algorithm 自检。从 hpa_algorithm.py 拆出,内容逐字节搬运。"""
from hpa_algorithm import *

# ---------------------------------------------------------------- 自检
def selfcheck() -> int:
    n = 0

    def ck(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    tol = Tolerances()

    # 1. tolerance 边界(1-scaleDown <= r <= 1+scaleUp)
    ck(tol.is_within(1.0), "1.0 必在容差内")
    ck(tol.is_within(0.90), "下界 0.9 含端点")
    ck(not tol.is_within(0.899), "0.899 越界")
    ck(tol.is_within(1.10), "上界 1.1 含端点")
    ck(not tol.is_within(1.101), "1.101 越界")
    ck(Tolerances(0.2, 0.05).is_within(1.15), "自定义容差应生效")
    ck(not Tolerances(0.2, 0.05).is_within(0.9), "下界 0.95 时 0.9 越界")

    # 2. usageRatio 的整数除法截断
    #    metricsTotal=100, requestsTotal=300 → 100*100/300 = 33 (非 33.33)
    ratio, util, raw = get_resource_utilization_ratio({"a": 100}, {"a": 300}, 50)
    ck(util == 33, f"整数除法应得 33, 实得 {util}")
    ck(abs(ratio - 33 / 50) < 1e-12, f"usageRatio 应为 33/50, 实得 {ratio}")
    # 对照:不截断会得到 33.33/50 = 0.6667,截断给出 0.66 —— 差别真实存在
    ck(abs(ratio - (100 * 100 / 300) / 50) > 1e-9, "截断与浮点结果必须不同(否则测试无意义)")

    # 3. 基础公式 desiredReplicas = ceil(current * curUsage / targetUsage)
    pods = Pods(ready=["p1", "p2", "p3"], unready=[], missing=[])
    metrics = {"p1": 200, "p2": 200, "p3": 200}
    reqs = {"p1": 100, "p2": 100, "p3": 100}
    rep, util, why = get_resource_replicas(3, 100, pods, metrics, reqs, tol)
    ck(util == 200, f"utilization 应为 200, 实得 {util}")
    ck(rep == 6, f"3*200/100=6, 实得 {rep}")
    ck(why == "plain", f"无 unready/missing 走 plain 分支, 实得 {why}")

    # 4. 容差内不动
    metrics2 = {"p1": 105, "p2": 105, "p3": 105}
    rep, util, why = get_resource_replicas(3, 100, pods, metrics2, reqs, tol)
    ck(util == 105, f"utilization 应为 105, 实得 {util}")
    ck(rep == 3 and why == "within tolerance", f"1.05 在容差内应维持 3, 实得 {rep}/{why}")

    # 5. 越容差才动:111 → ceil(1.11*3) = 4
    metrics3 = {"p1": 111, "p2": 111, "p3": 111}
    rep, _, why = get_resource_replicas(3, 100, pods, metrics3, reqs, tol)
    ck(rep == 4, f"ceil(1.11*3)=4, 实得 {rep}")

    # 6. scaleUpLimit = max(2*current, 4)
    ck(calculate_scale_up_limit(1) == 4, "current=1 → max(2,4)=4")
    ck(calculate_scale_up_limit(3) == 6, "current=3 → max(6,4)=6")
    ck(calculate_scale_up_limit(100) == 200, "current=100 → 200")
    # 单次最多翻倍:current=1,desired=10 → 只能到 4
    v, cond = normalize_desired_replicas(1, 10, 1, 100)
    ck(v == 4 and cond == "ScaleUpLimit", f"单次扩容上限 4, 实得 {v}/{cond}")
    v, cond = normalize_desired_replicas(1, 3, 1, 100)
    ck(v == 3 and cond == "DesiredWithinRange", f"3 在 [1,4] 内应放行, 实得 {v}/{cond}")
    v, cond = normalize_desired_replicas(5, 1, 2, 100)
    ck(v == 2 and cond == "TooFewReplicas", f"低于 min 应取 min, 实得 {v}/{cond}")
    v, cond = normalize_desired_replicas(20, 40, 1, 30)
    ck(v == 30 and cond == "TooManyReplicas",
       f"hpaMax=30 < scaleUpLimit=40 → 取 30, 实得 {v}/{cond}")

    # 7. 缺失指标 + 缩容:按 max(100, target)% 计 → 阻尼后回落到容差内,不动
    pods_m = Pods(ready=["p1", "p2", "p3"], unready=[], missing=["p4"])
    m_m = {"p1": 40, "p2": 40, "p3": 40}
    r_m = {"p1": 100, "p2": 100, "p3": 100, "p4": 100}
    rep, util, why = get_resource_replicas(4, 50, pods_m, m_m, r_m, tol)
    # 原始:120*100/300 = 40 → ratio 0.8 (<1,缩容);补 p4 = 100*100/100 = 100
    # 新:220*100/400 = 55 → ratio 1.1,恰在容差上界 → 维持
    ck(util == 40, f"原始 utilization 应为 40, 实得 {util}")
    ck(rep == 4, f"阻尼后 1.1 在容差内应维持 4, 实得 {rep}")
    ck(why == "damped/reversed -> hold", f"实得 {why}")
    # 对照组:若没有缺失 Pod,0.8 → ceil(0.8*3)=3,说明阻尼真的改变了结论
    rep_plain, _, _ = get_resource_replicas(4, 50, Pods(["p1", "p2", "p3"], [], []),
                                            m_m, {"p1": 100, "p2": 100, "p3": 100}, tol)
    ck(rep_plain == 3, f"无缺失时应缩到 3, 实得 {rep_plain}")

    # 8. 缺失指标 + 扩容:按 0% 计
    pods_u = Pods(ready=["p1"], unready=[], missing=["p2"])
    m_u = {"p1": 300}
    r_u = {"p1": 100, "p2": 100}
    rep, _, why = get_resource_replicas(2, 100, pods_u, m_u, r_u, tol)
    # 原始:300*100/100=300 → ratio 3.0;补 p2=0 → 300*100/200=150 → 1.5
    # 1.5 越容差、方向未反转 → ceil(1.5*2)=3
    ck(rep == 3, f"扩容缺失按 0%: 实得 {rep}")
    ck(why == "damped", f"实得 {why}")

    # 9. 未 Ready + 扩容:按 0% 计,且 requests 不均时阻尼效果显著
    pods_un = Pods(ready=["p1"], unready=["p2"], missing=[])
    m_un = {"p1": 260}
    r_un = {"p1": 100, "p2": 300}          # 故意不均
    rep, _, why = get_resource_replicas(2, 50, pods_un, m_un, r_un, tol)
    # 原始:260*100/100=260 → ratio 5.2;补 p2=0 → 260*100/400=65 → 1.3 → ceil(1.3*2)=3
    ck(rep == 3, f"unready 阻尼后应为 3, 实得 {rep}")
    # 若忽略阻尼:ceil(5.2*1) = 6 —— 差一倍
    ck(ceil_to_int32(5.2 * 1) == 6, "忽略阻尼的对照值应为 6")

    # 10. 方向反转 → 维持(usageRatio>1 但 newUsageRatio<1)
    pods_r = Pods(ready=["p1"], unready=["p2"], missing=[])
    m_r = {"p1": 100}
    r_r = {"p1": 100, "p2": 300}
    rep, _, why = get_resource_replicas(2, 50, pods_r, m_r, r_r, tol)
    # 原始 100*100/100=100 → 2.0;补 p2=0 → 100*100/400=25 → 0.5 → 方向反转 → 维持
    ck(rep == 2, f"方向反转应维持 2, 实得 {rep}")
    ck(why == "damped/reversed -> hold", f"实得 {why}")

    # 11. 多指标取最大
    cands = [3, 7, 5]
    ck(max(cands) == 7, "多指标取最大")

    # 12. 稳定窗口:缩容取窗口内最大,扩容取窗口内最小
    now = 1000.0
    recs = [Recommendation(2, now - 60), Recommendation(1, now - 10)]
    down = StabBehavior(DEFAULT_DOWNSCALE_STABILIZATION_SECONDS)
    up = StabBehavior(0)                                  # 扩容默认 0
    # current=5, desired=1;窗口内有 2 和 1 → down=max(1,2,1)=2 → 5>2 → 2
    ck(stabilize_recommendation(5, 1, now, recs, up, down) == 2,
       "缩容稳定窗口应取最大值 2")
    # 窗口外(>300s)的旧推荐不参与
    recs_old = [Recommendation(2, now - 400)]
    ck(stabilize_recommendation(5, 1, now, recs_old, up, down) == 1,
       "窗口外的推荐不应参与")
    # current=1, desired=4, 扩容窗口 60s 内有记录 2 → up=min(4,2)=2 → 1<2 → 2
    recs_up = [Recommendation(2, now - 10)]
    ck(stabilize_recommendation(1, 4, now, recs_up, StabBehavior(60), down) == 2,
       "扩容稳定窗口取最小值 2")
    # 扩容窗口为 0(默认)时 up_cutoff=now,任何历史记录都不生效 → 直接取 desired
    ck(stabilize_recommendation(1, 4, now, recs_up, StabBehavior(0), down) == 4,
       "扩容窗口 0 时不拖住,直接取 desired")

    # 13. 缩容稳定窗口默认值回填
    ck(maybe_init_scale_down_stabilization_window(None) == 300, "默认 300s")
    ck(maybe_init_scale_down_stabilization_window(60) == 60, "显式值不被覆盖")

    print(f"hpa_algorithm: {n} assertions passed")
    return n




if __name__ == "__main__":
    selfcheck()
