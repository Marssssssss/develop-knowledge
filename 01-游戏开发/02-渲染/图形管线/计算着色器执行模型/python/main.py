# -*- coding: utf-8 -*-
"""计算着色器(Compute Shader)执行模型软件模拟。

依据 Microsoft Learn "Compute Shader Overview" / "numthreads" / "Dispatch" 归纳:
  - 线程组织:Dispatch(gx,gy,gz) 启动 gx*gy*gz 个线程组;
    [numthreads(x,y,z)] 声明每组线程数,组内标识 SV_GroupThreadID,
    全局标识 SV_DispatchThreadID = GroupID*numthreads + GroupThreadID,
    组内线性索引 SV_GroupIndex = z*X*Y + y*X + x;
  - cs_5_0 限制:每组最多 1024 线程,Z 维最多 64,Dispatch 每维最多 65535 组;
    cs_4_x 更紧(768/16KB 共享内存/无原子指令);
  - groupshared 内存 + GroupMemoryBarrierWithGroupSync 实现
    线程组内协作的树形归约(parallel reduction);
  - 原子指令 InterlockedAdd 保证多线程累加不丢更新。
"""
import random

# ---------- 线程标识 ----------

class ThreadID:
    """一个线程的四个系统值标识(MS Learn 文档口径)。"""

    def __init__(self, numthreads, group_id, group_thread_id):
        self.group_id = group_id                    # SV_GroupID
        self.group_thread_id = group_thread_id      # SV_GroupThreadID
        self.dispatch_thread_id = tuple(            # SV_DispatchThreadID
            self.group_id[i] * numthreads[i] + self.group_thread_id[i] for i in range(3))
        x, y, _ = numthreads
        gx, gy, gz = group_thread_id
        self.group_index = gz * x * y + gy * x + gx  # SV_GroupIndex


def enumerate_threads(dispatch, numthreads):
    """枚举一次 Dispatch 的全部线程,验证 4 个标识的自洽性。"""
    out = []
    for gz in range(dispatch[2]):
        for gy in range(dispatch[1]):
            for gx in range(dispatch[0]):
                for tz in range(numthreads[2]):
                    for ty in range(numthreads[1]):
                        for tx in range(numthreads[0]):
                            out.append(ThreadID(numthreads, (gx, gy, gz), (tx, ty, tz)))
    return out


# ---------- cs_5_0 / cs_4_x 硬件限制(MS Learn 原文数字) ----------

CS5 = {
    "max_threads_per_group": 1024,
    "max_z": 64,
    "max_dispatch_dim": 65535,
    "groupshared_bytes": 32 * 1024,   # 32 KB
    "atomics": True,
}
CS4X = {
    "max_threads_per_group": 768,
    "max_z": 1,
    "max_dispatch_dim": 65535,
    "groupshared_bytes": 16 * 1024,   # 16 KB
    "atomics": False,
}


def validate_numthreads(numthreads, profile):
    x, y, z = numthreads
    if x * y * z > profile["max_threads_per_group"]:
        return False, "threads per group exceed %d" % profile["max_threads_per_group"]
    if z > profile["max_z"]:
        return False, "Z dimension %d exceed %d" % (z, profile["max_z"])
    return True, ""


def validate_dispatch(dispatch, profile):
    for d in dispatch:
        if d > profile["max_dispatch_dim"]:
            return False, "dispatch dimension exceed %d" % profile["max_dispatch_dim"]
    return True, ""


# ---------- groupshared + 屏障:树形归约 ----------

class ThreadGroupSim:
    """模拟一个线程组的 groupshared 内存与 GroupMemoryBarrierWithGroupSync。

    屏障语义:全部线程到齐前谁也不能越过 → 等价于"分阶段执行",
    每阶段内线程顺序任意、互不可见对方的本阶段写入。
    """

    def __init__(self, numthreads):
        self.numthreads = numthreads
        self.shared = {}          # groupshared 内存
        self.phase = 0           # 当前阶段号(每个 barrier 递增)

    def barrier(self):
        self.phase += 1

    def run_reduction(self, values):
        """树形归约:每线程写入自己的值,逐级折半,线程 0 写出组结果。"""
        x, y, z = self.numthreads
        n = x * y * z
        assert len(values) == n
        # 阶段 0:每线程写 groupshared[i] = values[i]
        for i in range(n):
            self.shared[i] = values[i]
        self.barrier()
        stride = n // 2
        while stride > 0:
            # 本阶段:线程 i(在其值有效时)做 shared[i] += shared[i+stride]
            for i in range(stride):
                self.shared[i] += self.shared[i + stride]
            self.barrier()
            stride //= 2
        return self.shared[0]


# ---------- 原子指令:InterlockedAdd ----------

def interlocked_add_example(values, use_atomic):
    """多线程对同一计数器累加:非原子会丢更新(模拟特定交错),原子不丢。

    用固定交错模拟:线程按两批交错执行读-改-写。
    """
    if use_atomic:
        counter = 0
        for v in values:  # 原子:读改写不可分割
            counter += v
        return counter
    # 非原子:所有线程先读到旧值 0,再各自写回自己的累加 → 只留最后写的
    # (这正是 GPU 上大量线程同时 RMW 同一地址的灾难形态)
    reads = [0] * len(values)
    writes = [r + v for r, v in zip(reads, values)]
    return max(writes) if writes else 0  # 丢失了除"最后写入"外的全部更新


# ---------- 自检 ----------

def main():
    # 1) MS Learn 文档的标准例子:Dispatch(5,3,2) + numthreads(10,8,3)
    nt = (10, 8, 3)
    tid = ThreadID(nt, (2, 1, 0), (7, 5, 0))
    assert tid.dispatch_thread_id == (27, 13, 0), tid.dispatch_thread_id
    assert tid.group_index == 57, tid.group_index
    # 全量枚举自检:DispatchThreadID 全局唯一且无洞
    threads = enumerate_threads((5, 3, 2), nt)
    assert len(threads) == 5 * 3 * 2 * 10 * 8 * 3
    seen = set()
    for t in threads:
        seen.add(t.dispatch_thread_id)
        # GroupIndex 与 GroupThreadID 一一对应(同一组内)
        x, y, z = t.group_thread_id
        assert t.group_index == z * nt[0] * nt[1] + y * nt[0] + x
    assert len(seen) == len(threads)  # DispatchThreadID 无重复

    # 2) 硬件限制校验(cs_5_0:≤1024/组,Z≤64;cs_4_x:≤768,Z≤1)
    ok, _ = validate_numthreads((1024, 1, 1), CS5)
    assert ok
    ok, _ = validate_numthreads((32, 32, 1), CS5)   # 1024 恰好
    assert ok
    ok, _ = validate_numthreads((33, 33, 1), CS5)   # 1089 超限
    assert not ok
    ok, _ = validate_numthreads((64, 1, 65), CS5)    # Z=65 超限
    assert not ok
    ok, _ = validate_numthreads((768, 1, 1), CS4X)
    assert ok
    ok, _ = validate_numthreads((256, 1, 2), CS4X)   # cs_4_x 的 Z 只能是 1
    assert not ok
    ok, _ = validate_dispatch((65535, 1, 1), CS5)
    assert ok
    ok, _ = validate_dispatch((65536, 1, 1), CS5)
    assert not ok

    # 3) 树形归约:groupshared + barrier 求和正确(含随机数据)
    rng = random.Random(2026)
    for nt_i in ((16, 16, 1), (256, 1, 1), (8, 4, 2)):
        n = nt_i[0] * nt_i[1] * nt_i[2]
        vals = [rng.randint(-100, 100) for _ in range(n)]
        sim = ThreadGroupSim(nt_i)
        got = sim.run_reduction(vals)
        assert got == sum(vals), (nt_i, got, sum(vals))
        assert len(sim.shared) == n

    # 4) 屏障次序:线程 0 写 groupshared,屏障后其他线程才保证可见;
    #    无屏障时,消费线程若在线程 0 的写之前执行,读到旧值 0
    class SharedMem:
        def __init__(self):
            self.x = 0

        def with_barrier(self):
            # 阶段 1:线程 0 写;阶段 2(屏障后):线程 1 读 → 必见新值
            self.x = 42
            self.barrier_count += 1   # GroupMemoryBarrierWithGroupSync
            return self.x

        def without_barrier(self):
            # 交错:线程 1 的读被调度在线程 0 的写之前 → 读到旧值
            old = self.x              # 线程 1 先读
            self.x = 42               # 线程 0 后写
            return old

        barrier_count = 0

    sm = SharedMem()
    assert sm.with_barrier() == 42
    assert sm.barrier_count == 1
    sm2 = SharedMem()
    assert sm2.without_barrier() == 0  # 读发生在写之前 → 旧值

    # 5) 原子指令:非原子丢更新,InterlockedAdd 不丢
    vals = [10, 20, 30, 40]
    assert interlocked_add_example(vals, use_atomic=True) == 100
    assert interlocked_add_example(vals, use_atomic=False) == 40  # 只剩最后一个写
    assert CS4X["atomics"] is False and CS5["atomics"] is True

    print("ALL TESTS PASSED")
    print("Dispatch(5,3,2)+numthreads(10,8,3): GroupThreadID(7,5,0) -> "
          "DispatchThreadID(27,13,0), GroupIndex 57")
    print("reduction checks passed for (16,16,1) / (256,1,1) / (8,4,2)")


if __name__ == "__main__":
    main()
