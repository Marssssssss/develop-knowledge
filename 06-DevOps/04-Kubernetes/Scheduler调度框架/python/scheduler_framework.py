"""
kube-scheduler Scheduling Framework 最小实现。

权威来源(实际读过):
  1. https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
  2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/scheduler/framework/interface.go

建模要点(全部来自上述两处原文):
  - 一次调度 = 「调度周期(scheduling cycle)」+「绑定周期(binding cycle)」,
    合称一个 scheduling context。调度周期串行,绑定周期可并发。
  - 扩展点顺序:PreEnqueue → QueueSort → PreFilter → Filter → PostFilter →
    PreScore → Score → NormalizeScore → Reserve → Permit → PreBind → Bind → PostBind
  - Filter:对每个 node 按配置顺序调用;**任一 plugin 判定 infeasible 后,
    该 node 剩下的 plugin 不再被调用**;node 之间可并发。
  - PostFilter:**仅在 Filter 之后没有任何可行 node 时**才调用;典型实现是抢占;
    任一 plugin 标记 Schedulable 后剩余 plugin 不再调用。
  - NormalizeScore:每个 plugin 每轮调度调用一次,拿到的是**同一 plugin** 的
    全部 node 分数;官方示例是 score*NodeScoreMax/highest。
  - Reserve 失败 → 后续 plugin 不执行且 Reserve 阶段失败 → **所有** Reserve plugin
    的 Unreserve 按 Reserve 调用的**逆序**执行;Unreserve 必须幂等且不可失败。
  - Permit 三态:approve / deny / wait(带超时);deny 或超时都会触发 Unreserve。
  - Bind:全部 PreBind 完成后按序调用;**某个 plugin 选择处理该 Pod 后,
    其余 bind plugin 被跳过**。
"""
from typing import List, Dict, Optional, Tuple

NODESCORE_MAX = 100


class Pod:
    def __init__(self, name: str, cpu: int, prio: int = 0):
        self.name = name
        self.cpu = cpu
        self.prio = prio


class Node:
    def __init__(self, name: str, alloc_cpu: int, pods: Optional[List[Pod]] = None):
        self.name = name
        self.alloc_cpu = alloc_cpu          # 可分配 CPU(简化为整数)
        self.pods = pods if pods is not None else []   # 已占用该 node 的 Pod

    def used(self) -> int:
        return sum(p.cpu for p in self.pods)


class Plugin:
    """一个插件可以注册多个扩展点。默认实现全部是 no-op / 放行。"""

    def __init__(self, name: str, weight: int = 1):
        self.name = name
        self.weight = weight

    # --- scheduling cycle ---
    def prefilter(self, fw, pod) -> Optional[str]:
        return None                      # None=通过, str=中止原因

    def filter(self, fw, pod, node) -> Optional[str]:
        return None                      # None=feasible, str=不可行原因

    def postfilter(self, fw, pod, status_map) -> Optional[str]:
        return None                      # None=未成功, str=提名 node(抢占成功)

    def prescore(self, fw, pod, nodes):
        pass

    def score(self, fw, pod, node) -> int:
        return 0

    def normalizescore(self, fw, scores: Dict[str, int]) -> Dict[str, int]:
        return scores

    def reserve(self, fw, pod, node) -> bool:
        return True

    def unreserve(self, fw, pod, node):
        pass

    def permit(self, fw, pod, node) -> str:
        return "approve"

    # --- binding cycle ---
    def prebind(self, fw, pod, node) -> bool:
        return True

    def bind(self, fw, pod, node) -> bool:
        return False                     # False=不处理, True=已接管(后续跳过)

    def postbind(self, fw, pod, node):
        pass


class Framework:
    def __init__(self, plugins: List[Plugin]):
        self.plugins = plugins
        self.trace: List[Tuple] = []
        self.state: Dict = {}
        self.unreserve_calls: List[str] = []

    def _unreserve_all(self, pod, node, reserved: List[Plugin]):
        for p in reversed(reserved):
            self.trace.append(("Unreserve", p.name))
            self.unreserve_calls.append(p.name)
            p.unreserve(self, pod, node)

    def schedule(self, pod: Pod, nodes: List[Node]) -> Dict:
        self.trace.clear()
        self.unreserve_calls.clear()
        self.trace.append(("QueueSort", "-"))
        out = {"node": None, "nominated": None, "phase": "unschedulable"}

        # PreFilter:出错即中止整个调度周期
        for p in self.plugins:
            self.trace.append(("PreFilter", p.name))
            err = p.prefilter(self, pod)
            if err:
                out["phase"] = "prefilter_abort"
                return out

        # Filter:node 内短路
        feasible: List[Node] = []
        status_map: Dict[str, str] = {}
        for n in nodes:
            rejected = None
            for p in self.plugins:
                self.trace.append(("Filter", p.name, n.name))
                reason = p.filter(self, pod, n)
                if reason:
                    rejected = reason
                    status_map[n.name] = reason
                    break                      # 该 node 剩下的 plugin 不再调用
            if rejected is None:
                feasible.append(n)

        # PostFilter:仅当没有可行 node
        if not feasible:
            for p in self.plugins:
                self.trace.append(("PostFilter", p.name))
                nn = p.postfilter(self, pod, status_map)
                if nn:
                    out["nominated"] = nn
                    out["phase"] = "nominated"     # 抢占提名,本次不绑定
                    return out
            return out

        # PreScore → Score → NormalizeScore
        for p in self.plugins:
            self.trace.append(("PreScore", p.name))
            p.prescore(self, pod, feasible)
        raw: Dict[str, Dict[str, int]] = {}
        for p in self.plugins:
            self.trace.append(("Score", p.name))
            raw[p.name] = {n.name: p.score(self, pod, n) for n in feasible}
        norm: Dict[str, Dict[str, int]] = {}
        for p in self.plugins:
            self.trace.append(("NormalizeScore", p.name))
            norm[p.name] = p.normalizescore(self, raw[p.name])

        total_w = sum(p.weight for p in self.plugins) or 1
        final: Dict[str, float] = {n.name: 0.0 for n in feasible}
        for p in self.plugins:
            for n in feasible:
                final[n.name] += p.weight * norm[p.name][n.name] / total_w
        best = sorted(feasible, key=lambda n: (-final[n.name], n.name))[0]
        out["node"] = best.name

        # Reserve:失败则逆序 Unreserve
        reserved: List[Plugin] = []
        for p in self.plugins:
            self.trace.append(("Reserve", p.name))
            if not p.reserve(self, pod, best):
                self._unreserve_all(pod, best, reserved)
                return out                     # phase 仍为 unschedulable
            reserved.append(p)

        # Permit:approve / deny / wait
        for p in self.plugins:
            self.trace.append(("Permit", p.name))
            d = p.permit(self, pod, best)
            if d == "deny":
                self._unreserve_all(pod, best, reserved)
                return out
            if d == "wait":
                self._unreserve_all(pod, best, reserved)
                out["phase"] = "waiting"
                return out

        for p in self.plugins:
            self.trace.append(("PreBind", p.name))
            if not p.prebind(self, pod, best):
                self._unreserve_all(pod, best, reserved)
                return out

        for p in self.plugins:
            self.trace.append(("Bind", p.name))
            if p.bind(self, pod, best):
                break                          # 接管的 plugin 之后的全部跳过

        for p in self.plugins:
            self.trace.append(("PostBind", p.name))
            p.postbind(self, pod, best)
        out["phase"] = "bound"
        out["scores"] = final
        out["norm"] = norm
        return out


# ---------------------------------------------------------------- 示例插件
class NodeResourcesFit(Plugin):
    """Filter: alloc - used >= pod.cpu"""

    def filter(self, fw, pod, node):
        if node.alloc_cpu - node.used() < pod.cpu:
            return "Insufficient cpu"
        return None


class NodeUnschedulable(Plugin):
    """Filter: node 被标记 unschedulable(简化:名字以 'taint-' 开头)"""

    def filter(self, fw, pod, node):
        if node.name.startswith("taint-"):
            return "node(s) had untolerated taint"
        return None


class LeastAllocated(Plugin):
    """Score: 剩余比例越高分越高;NormalizeScore 拉到 NodeScoreMax"""

    def score(self, fw, pod, node):
        free = node.alloc_cpu - node.used()
        return int(free * 100 / node.alloc_cpu)

    def normalizescore(self, fw, scores):
        highest = max(scores.values()) or 1
        return {k: v * NODESCORE_MAX // highest for k, v in scores.items()}


class DefaultPreemption(Plugin):
    """PostFilter: 抢占 —— 从 status_map 里挑一个"驱逐低优先级 Pod 后能放下"的 node"""

    def __init__(self, cluster: Dict[str, Node]):
        super().__init__("DefaultPreemption")
        self.cluster = cluster

    def postfilter(self, fw, pod, status_map):
        for name in sorted(status_map):
            n = self.cluster[name]
            victims = [p for p in n.pods if p.prio < pod.prio]
            freed = sum(v.cpu for v in victims)
            if n.alloc_cpu - (n.used() - freed) >= pod.cpu:
                return name
        return None


class VolumeBinder(Plugin):
    """Reserve 成功/失败可注入;Bind 会接管 Pod(验证 Bind 短路)"""

    def __init__(self, reserve_ok=True):
        super().__init__("VolumeBinder", weight=1)
        self.reserve_ok = reserve_ok
        self.reserved = []

    def reserve(self, fw, pod, node):
        self.reserved.append(node.name)
        return self.reserve_ok

    def unreserve(self, fw, pod, node):
        if self.reserved:
            self.reserved.pop()

    def bind(self, fw, pod, node):
        return True


class DenyAll(Plugin):
    def permit(self, fw, pod, node):
        return "deny"


class WaitForever(Plugin):
    def permit(self, fw, pod, node):
        return "wait"


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
