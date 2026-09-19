# -*- coding: utf-8 -*-
"""scheduler_framework 自检。从 scheduler_framework.py 拆出,内容逐字节搬运。"""
from scheduler_framework import *

# ---------------------------------------------------------------- 自检
def _assert_count():
    import traceback
    return None


def selfcheck():
    n_ok = 0

    def ck(cond, msg):
        nonlocal n_ok
        assert cond, msg
        n_ok += 1

    nodes = [Node("n1", 8), Node("n2", 4, [Pod("a", 3)])]
    fw = Framework([NodeResourcesFit("NodeResourcesFit"), NodeUnschedulable("NodeUnschedulable")])
    r = fw.schedule(Pod("p", 2), nodes)

    # 1. 扩展点顺序
    seq = [t[0] for t in fw.trace]
    firsts = []
    for s in seq:
        if not firsts or firsts[-1] != s:
            firsts.append(s)
    ck(firsts == ["QueueSort", "PreFilter", "Filter", "PreScore", "Score",
                  "NormalizeScore", "Reserve", "Permit", "PreBind", "Bind",
                  "PostBind"], f"扩展点顺序错误: {firsts}")
    ck("PostFilter" not in seq, "有可行 node 时不应调用 PostFilter")
    ck(r["phase"] == "bound", f"应绑定成功, 实得 {r['phase']}")

    # 2. Filter node 内短路:n2 只剩 1 CPU,放不下 2 CPU 的 Pod → NodeUnschedulable 不该被调用
    n2_traces = [t for t in fw.trace if t[0] == "Filter" and len(t) > 2 and t[2] == "n2"]
    ck([t[1] for t in n2_traces] == ["NodeResourcesFit"],
       f"n2 应在第一个 Filter 处短路: {n2_traces}")
    n1_traces = [t for t in fw.trace if t[0] == "Filter" and len(t) > 2 and t[1] == "NodeUnschedulable"]
    ck(len(n1_traces) == 1 and n1_traces[0][2] == "n1", "n1 应通过全部 Filter")

    # 3. NormalizeScore:官方 Score 归一化后最高分 = NodeScoreMax
    fw2 = Framework([NodeResourcesFit("NodeResourcesFit"), LeastAllocated("LeastAllocated")])
    r2 = fw2.schedule(Pod("p", 1), nodes)          # 1 CPU 时两个 node 都可行
    nm = r2["norm"]["LeastAllocated"]
    # n1 剩 8/8=100, n2 剩 (4-3)/4=25;归一化 highest=100 → n1=100, n2=25
    ck(nm["n1"] == NODESCORE_MAX, f"归一化后最高分应为 {NODESCORE_MAX}, 实得 {nm}")
    ck(nm["n2"] == 25, f"n2 归一化后期望 25, 实得 {nm['n2']}")
    sc = r2["scores"]
    # NodeResourcesFit 不打分(恒 0),权重各 1 → 最终分 = 归一化分 / 2
    ck(abs(sc["n1"] - 50.0) < 1e-9, f"n1 最终分期望 50, 实得 {sc['n1']}")
    ck(abs(sc["n2"] - 12.5) < 1e-9, f"n2 最终分期望 12.5, 实得 {sc['n2']}")
    ck(r2["node"] == "n1", f"应选最高分 n1, 实得 {r2['node']}")

    # 4. PostFilter 抢占:两个 node 都被低优先级 Pod 占满,高优先级 Pod 触发提名
    cluster = {
        "taint-1": Node("taint-1", 4, [Pod("lo", 4, prio=0)]),
        "taint-2": Node("taint-2", 4, [Pod("lo2", 4, prio=0)]),
    }
    hp = Pod("hi", 2, prio=100)
    fw3 = Framework([NodeResourcesFit("NodeResourcesFit"),
                     NodeUnschedulable("NodeUnschedulable"),
                     DefaultPreemption(cluster)])
    r3 = fw3.schedule(hp, list(cluster.values()))
    ck("PostFilter" in [t[0] for t in fw3.trace], "无可行 node 必须调用 PostFilter")
    ck(r3["nominated"] == "taint-1", f"应提名 taint-1, 实得 {r3['nominated']}")
    ck(r3["node"] is None, "PostFilter 只提名,本次不绑定")

    # 5. 抢占失败:入站 Pod 优先级不高于 victim
    lo = Pod("lo2", 2, prio=0)
    r5 = fw3.schedule(lo, list(cluster.values()))
    ck(r5["nominated"] is None, "优先级不高于 victim 时不应抢占成功")
    ck(r5["node"] is None, "抢占失败不应绑定")

    # 6. Reserve 失败 → 逆序 Unreserve
    vb = VolumeBinder(reserve_ok=False)
    fw6 = Framework([NodeResourcesFit("NodeResourcesFit"), vb])
    r6 = fw6.schedule(Pod("p", 1), [Node("n1", 4)])
    ck(r6["phase"] == "unschedulable", f"Reserve 失败不应绑定: {r6['phase']}")
    # 失败的 VolumeBinder 自身不入栈,但先成功的 NodeResourcesFit 要被回滚
    ck(fw6.unreserve_calls == ["NodeResourcesFit"],
       f"失败前已 Reserve 的插件必须回滚: {fw6.unreserve_calls}")

    class R1(Plugin):
        def reserve(self, fw, pod, node):
            fw.state.setdefault("r", []).append("R1")
            return True

        def unreserve(self, fw, pod, node):
            fw.state.setdefault("u", []).append("R1")

    class R2(Plugin):
        def reserve(self, fw, pod, node):
            fw.state.setdefault("r", []).append("R2")
            return False          # 第二个失败

        def unreserve(self, fw, pod, node):
            fw.state.setdefault("u", []).append("R2")

    fw7 = Framework([R1("R1"), R2("R2")])
    r7 = fw7.schedule(Pod("p", 1), [Node("n1", 4)])
    ck(fw7.state.get("r") == ["R1", "R2"], f"Reserve 应按序调用: {fw7.state.get('r')}")
    ck(fw7.state.get("u") == ["R1"], f"Unreserve 只回滚已成功的 R1: {fw7.state.get('u')}")
    ck(r7["phase"] == "unschedulable", "Reserve 阶段失败不应绑定")

    # 7. Permit deny → Unreserve
    fw8 = Framework([R1("R1"), DenyAll("DenyAll"), VolumeBinder()])
    r8 = fw8.schedule(Pod("p", 1), [Node("n1", 4)])
    ck(r8["phase"] == "unschedulable", f"Permit deny 不应绑定: {r8['phase']}")
    # 全部 Reserve plugin(含 no-op)按 Reserve 调用的逆序回滚
    ck(fw8.unreserve_calls == ["VolumeBinder", "DenyAll", "R1"],
       f"deny 应逆序 Unreserve 全部 Reserve plugin: {fw8.unreserve_calls}")

    # 8. Permit wait → 进入 waiting,同样回滚
    fw9 = Framework([R1("R1"), WaitForever("WaitForever"), VolumeBinder()])
    r9 = fw9.schedule(Pod("p", 1), [Node("n1", 4)])
    ck(r9["phase"] == "waiting", f"wait 应停在 waiting: {r9['phase']}")
    ck(fw9.unreserve_calls == ["VolumeBinder", "WaitForever", "R1"],
       f"wait 转 deny 后逆序回滚: {fw9.unreserve_calls}")

    # 9. Bind 短路:VolumeBinder 接管后,后一个 Bind plugin 不应被调用
    class NeverBind(Plugin):
        def bind(self, fw, pod, node):
            fw.state["never"] = True
            return True

    nb = NeverBind("NeverBind")
    fw10 = Framework([VolumeBinder("VolumeBinder"), nb])
    r10 = fw10.schedule(Pod("p", 1), [Node("n1", 4)])
    ck(r10["phase"] == "bound", "应绑定成功")
    ck("never" not in fw10.state, "第一个接管的 Bind plugin 之后应全部跳过")
    ck("PostBind" in [t[0] for t in fw10.trace], "绑定成功后应调用 PostBind")

    print(f"scheduler_framework: {n_ok} assertions passed")
    return n_ok




if __name__ == "__main__":
    selfcheck()
