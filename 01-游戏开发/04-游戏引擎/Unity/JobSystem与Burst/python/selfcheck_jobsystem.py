"""selfcheck_jobsystem.py — jobsystem.py 的自检（与模型分离，见 OPTIMIZATION.md §1.1）。"""

from __future__ import annotations

from typing import Dict

from jobsystem import *  # noqa: F401,F403

_ASSERTIONS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    _ASSERTIONS += 1
    print(f"ok {_ASSERTIONS:>2} {label}" + (f"  [{detail}]" if detail else ""))


def main() -> None:
    # 1) NativeContainer 没有 ref return：arr[0] += 1 写不回去
    a = NativeArray(4)
    a[0] = 5.0
    temp = a[0]
    temp += 1
    check("NativeArray 无 ref return：arr[0]+=1 不生效", a[0] == 5.0, f"a[0]={a[0]}")
    a[0] = temp
    check("必须读-改-写回才生效", a[0] == 6.0, f"a[0]={a[0]}")

    # 2) job 数据被复制：job 结构里的普通字段改了不回传，NativeContainer 才共享
    b = NativeArray(2)
    caller_data = {"scale": 1.0}

    def job_body(state: Dict[str, float]) -> None:
        state["scale"] = 99.0          # 只改到 job 数据的副本
        b[0] = 42.0                    # NativeContainer 与原对象指向同一块内存 → 可见

    js = JobSystem()
    h = js.schedule(Job("write", writes={b.uid}, data=dict(caller_data), run=job_body))
    js.complete(h)
    check("job 内改普通字段不回传（job 数据被复制）", caller_data["scale"] == 1.0,
          f"caller={caller_data['scale']}")
    check("只有写进 NativeContainer 的结果可见", b[0] == 42.0, f"b[0]={b[0]}")

    # 3) 安全系统：两个 job 同时写同一容器 → 调度第二个时抛错
    c = NativeArray(4)
    js2 = JobSystem()
    h1 = js2.schedule(Job("A", writes={c.uid}))
    conflict = ""
    try:
        js2.schedule(Job("B", writes={c.uid}))
    except SafetyException as e:
        conflict = str(e)
    check("写-写冲突在调度时抛出", conflict.startswith("B 与 A"), conflict)

    # 4) 读-读可并行；读-写必须靠依赖
    d = NativeArray(4)
    js3 = JobSystem()
    r1 = js3.schedule(Job("R1", reads={d.uid}))
    r2 = js3.schedule(Job("R2", reads={d.uid}))
    check("读-读可并行调度（同一容器挂两个读者）",
          len(js3.pending_readers[d.uid]) == 2, f"{len(js3.pending_readers[d.uid])}")
    conflict = ""
    try:
        js3.schedule(Job("W", writes={d.uid}))
    except SafetyException as e:
        conflict = str(e)
    check("读-写冲突在调度时抛出", "冲突" in conflict, conflict)
    w_ok = js3.schedule(Job("W2", writes={d.uid}, deps=JobSystem.combine([r1, r2])))
    check("声明依赖后同一容器可写", w_ok.id > 0)
    js3.complete(w_ok)
    check("依赖顺序：两个读 job 先于写 job 执行",
          js3.executed.index("R1") < js3.executed.index("W2")
          and js3.executed.index("R2") < js3.executed.index("W2"), str(js3.executed))

    # 5) Complete 清理安全系统状态，不 Complete 即泄漏
    check("Complete 后从泄漏表移除", "write" not in js.leaked, str(js.leaked))
    e = NativeArray(2)
    js4 = JobSystem()
    js4.schedule(Job("forgot", writes={e.uid}))
    check("未 Complete 的 handle 留在泄漏表里", js4.leaked == ["forgot"], str(js4.leaked))

    # 6) ParallelFor 分批 + work stealing（一次偷一半）
    batches = split_batches(1000, 64)
    check("批数 = ceil(N / batchCount)", len(batches) == (1000 + 63) // 64, f"{len(batches)}")
    check("每批不超过 batchCount", all(end - start <= 64 for start, end in batches))
    check("批次拼接覆盖全部下标",
          [i for s, t in batches for i in range(s, t)] == list(range(1000)))
    imbalanced = split_batches(1000, 1)          # batch_count=1 → 1000 批，最细粒度
    check("batch_count=1 时批数 = N", len(imbalanced) == 1000)
    res = steal_schedule(imbalanced[:200], workers=4)
    steals2 = res["steals"]                      # type: ignore[assignment]
    check("发生了 work stealing", len(steals2) > 0, f"{len(steals2)} 次")
    check("每一次偷取都只拿走剩余批次的一半",
          all(take == max(1, remaining // 2) for _w, _v, take, remaining in steals2),
          str(steals2))
    check("偷完仍不丢不重：全部批次都被执行",
          res["processed"] == 200 and res["left"] == [0, 0, 0, 0],
          f"processed={res['processed']} left={res['left']}")

    # 7) 并行 job 每个下标恰好执行一次
    out = NativeArray(1000)
    hits: List[int] = []

    def body(i: int, state: Dict[str, float]) -> None:
        out[i] = out[i] + 1
        hits.append(i)

    js5 = JobSystem()
    h5 = js5.schedule(Job("parallel", writes={out.uid}, parallel_length=1000,
                          batch_count=64, run=body))
    js5.complete(h5)
    check("并行 job 覆盖全部下标恰好一次", sorted(hits) == list(range(1000)) and len(hits) == 1000)

    # 8) 分配器寿命
    tr = AllocatorTracker()
    temp_arr = NativeArray(1, "Temp")
    tempjob_arr = NativeArray(1, "TempJob")
    persist_arr = NativeArray(1, "Persistent")
    for arr in (temp_arr, tempjob_arr, persist_arr):
        tr.track(arr)
    for _ in range(3):
        tr.end_frame()
    check("3 帧内 TempJob 无告警", not any("TempJob" in w for w in tr.warnings), str(tr.warnings))
    check("Temp 超过 1 帧即告警", any("Temp 存活" in w for w in tr.warnings), str(tr.warnings))
    for _ in range(2):
        tr.end_frame()
    check("TempJob 超过 4 帧告警", any("TempJob 存活 5 帧" in w for w in tr.warnings), str(tr.warnings))
    check("Persistent 永不告警", not any("Persistent" in w for w in tr.warnings))

    # 9) Burst/HPC# 类型子集
    check("Burst 支持 blittable 基元", all(burst_supports(t) for t in ["float", "int", "double", "bool"]))
    check("Burst 不支持 char/decimal/string/托管数组",
          all(not burst_supports(t) for t in ["char", "decimal", "string", "managed array"]))
    check("struct 与 fixed 数组字段可编译", burst_field_ok("struct") and burst_field_ok("struct+fixed"))
    check("class（引用类型）不能进 Burst job", not burst_field_ok("class"))
    check("静态只读托管数组是特例（普通托管数组不行）",
          burst_field_ok("static readonly managed array") and not burst_field_ok("managed array"))
    check("多维数组不被支持", not burst_field_ok("multi-dimensional array"))

    print(f"\n全部 {_ASSERTIONS} 条断言通过")


if __name__ == "__main__":
    main()
