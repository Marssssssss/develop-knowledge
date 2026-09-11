"""DAG 流水线调度 — Kahn 拓扑排序 + needs 依赖语义 + 关键路径 + 失败传播.

模拟 GitLab CI `needs` / GitHub Actions `jobs.<id>.needs` 的 DAG 调度核心:
  1. 分层拓扑排序(可并行层)      demo_diamond
  2. 环检测(流水线创建失败)      demo_cycle
  3. stage barrier vs DAG 时长   demo_barrier_vs_dag
  4. 失败传播(依赖者被 SKIPPED)  demo_failure
"""
from __future__ import annotations


class Job:
    __slots__ = ("name", "stage", "duration", "needs")

    def __init__(self, name: str, stage: str, duration: int, needs: list[str]):
        self.name = name
        self.stage = stage
        self.duration = duration          # 模拟执行时长(分钟)
        self.needs = needs                # 依赖的 job 名列表(needs 语义)


def by_name(jobs: list[Job], name: str) -> Job:
    for j in jobs:
        if j.name == name:
            return j
    raise KeyError(name)


def topo_layers(jobs: list[Job]) -> list[list[str]]:
    """Kahn 分层拓扑排序:返回按层分组的 job 名;有环抛 ValueError.

    每层内 job 互不依赖,可并行执行;层数 = 无限并发下的最少"轮次"。
    """
    names = {j.name for j in jobs}
    succ: dict[str, list[str]] = {j.name: [] for j in jobs}
    in_deg = {j.name: 0 for j in jobs}
    for j in jobs:
        for n in j.needs:
            if n not in names:
                raise ValueError(
                    f"job '{j.name}' depends on unknown job '{n}'")
            succ[n].append(j.name)       # 边 need -> j
            in_deg[j.name] += 1
    queue = sorted(n for n, d in in_deg.items() if d == 0)
    layers: list[list[str]] = []
    done = 0
    while queue:
        layers.append(queue)
        done += len(queue)
        nxt: list[str] = []
        for u in queue:                   # 这一批 job "完成"
            for v in succ[u]:             # 删出边:后继入度减 1
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    nxt.append(v)
        queue = sorted(nxt)               # 排序保证输出可复现
    if done != len(jobs):                 # 有节点永远到不了 0 入度 => 环
        cyc = sorted(n for n, d in in_deg.items() if d > 0)
        raise ValueError("cycle detected among jobs: " + ", ".join(cyc))
    return layers


def earliest_finish(jobs: list[Job]) -> dict[str, int]:
    """关键路径递推:ef[j] = dur[j] + max(ef[need]);无依赖时 max 取 0."""
    ef: dict[str, int] = {}
    for layer in topo_layers(jobs):      # 拓扑序保证 need 先于 j 计算
        for name in layer:
            j = by_name(jobs, name)
            ef[name] = j.duration + max(
                (ef[n] for n in j.needs), default=0)
    return ef


def critical_path(jobs: list[Job]) -> list[str]:
    """从 ef 最大的 job 回溯 max 来源,得到关键路径。"""
    ef = earliest_finish(jobs)
    cur = max(ef, key=ef.get)             # type: ignore[arg-type]
    path = [cur]
    while True:
        j = by_name(jobs, cur)
        preds = [n for n in j.needs if n in ef]
        if not preds:
            return path
        cur = max(preds, key=lambda n: ef[n])
        path.insert(0, cur)


def stage_barrier_time(jobs: list[Job]) -> int:
    """stage barrier:前一 stage 全部完成,下一 stage 才开始。"""
    order: list[str] = []
    for j in jobs:                        # stage 按首次出现顺序
        if j.stage not in order:
            order.append(j.stage)
    total = 0
    for st in order:
        total += max(j.duration for j in jobs if j.stage == st)
    return total


def simulate_run(jobs: list[Job], fail: set[str]) -> None:
    """按拓扑序执行;失败 job 的传递依赖者被 SKIPPED(GitHub Actions 语义)."""
    status = {j.name: "PENDING" for j in jobs}
    for layer in topo_layers(jobs):
        for name in layer:
            j = by_name(jobs, name)
            bad = [n for n in j.needs
                   if status[n] in ("FAILED", "SKIPPED")]
            if bad:
                status[name] = "SKIPPED"
                print(f"  [skip]  {name:<14} SKIPPED "
                      f"(need '{bad[0]}' {status[bad[0]]})")
                continue
            if name in fail:
                status[name] = "FAILED"
                print(f"  [fail]  {name:<14} FAILED  (exit code 1)")
            else:
                status[name] = "SUCCESS"
                print(f"  [ok]    {name:<14} SUCCESS ({j.duration} min)")


def demo_diamond() -> None:
    """GitLab 文档的菱形依赖算例:build 扇出 3 个测试再扇入 deploy。"""
    print("== demo 1: 菱形依赖(DAG) ==")
    jobs = [
        Job("build",          "build", 3, []),
        Job("test_unit",      "test",  2, ["build"]),
        Job("test_intg",      "test",  4, ["build"]),
        Job("test_perf",      "test",  5, ["build"]),
        Job("deploy",         "deploy", 2, ["test_unit", "test_intg", "test_perf"]),
    ]
    for i, layer in enumerate(topo_layers(jobs)):
        print(f"  layer {i}: {layer}")
    ef = earliest_finish(jobs)
    print(f"  earliest_finish: {ef}")
    print(f"  critical path: {' -> '.join(critical_path(jobs))}"
          f"  (wall-clock lower bound = {max(ef.values())} min)")


def demo_cycle() -> None:
    print("== demo 2: needs 成环(创建即失败) ==")
    jobs = [
        Job("build",  "build", 1, ["deploy"]),   # 故意成环
        Job("test",   "test",  1, ["build"]),
        Job("deploy", "deploy", 1, ["test"]),
    ]
    try:
        topo_layers(jobs)
    except ValueError as e:
        print(f"  pipeline creation failed: {e}")


def demo_barrier_vs_dag() -> None:
    """GitLab 博客算例:build_a 1min / build_b 5min;test_c 只依赖 build_a。"""
    print("== demo 3: stage barrier vs DAG ==")
    jobs = [
        Job("build_a", "build",  1, []),
        Job("build_b", "build",  5, []),
        Job("test_c",  "test",   2, ["build_a"]),
        Job("deploy",  "deploy", 1, ["test_c"]),
    ]
    barrier = stage_barrier_time(jobs)
    ef = earliest_finish(jobs)
    dag = max(ef.values())
    print(f"  stage barrier: {barrier} min  (build 5 + test 2 + deploy 1)")
    print(f"  DAG(needs):    {dag} min   (关键路径 "
          f"{' -> '.join(critical_path(jobs))})")
    print(f"  节省: {barrier - dag} min — test_c 无需等 build_b")


def demo_failure() -> None:
    print("== demo 4: 失败传播(test_a 失败 -> deploy_a 跳过) ==")
    jobs = [
        Job("build",    "build",  1, []),
        Job("test_a",   "test",   2, ["build"]),
        Job("test_b",   "test",   2, ["build"]),
        Job("deploy_a", "deploy", 1, ["test_a"]),
        Job("deploy_b", "deploy", 1, ["test_b"]),
    ]
    simulate_run(jobs, fail={"test_a"})


if __name__ == "__main__":
    demo_diamond()
    print()
    demo_cycle()
    print()
    demo_barrier_vs_dag()
    print()
    demo_failure()
