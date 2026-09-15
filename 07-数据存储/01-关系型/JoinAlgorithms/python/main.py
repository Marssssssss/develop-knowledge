#!/usr/bin/env python3
"""Join 算法最小实现:nested loop / block nested loop / hash join /
grace hash join(分区) / sort-merge join。

依据 CMU 15-445 (Spring 2023) Lecture 11 "Join Algorithms" 讲义归纳:
- Hash Join: build 阶段对外表(小表)建哈希表,probe 阶段扫描内表探测;
  内存放不下时用 GRACE 分区哈希连接(两表同哈希分区,逐对分区再 join),
  成本 3(M+N);hybrid 优化 = 热分区留内存。
- Sort-Merge Join: 两表按 join key 排序后双指针归并,重复键需回溯;
  成本 M + N + sort cost。
"""
import math


def simple_nested_loop(R, S):
    """朴素嵌套循环:外表每条元组扫全表内表。成本 M + m*N。"""
    out, probes = [], 0
    for r in R:
        for s in S:
            probes += 1
            if r[0] == s[0]:
                out.append((r, s))
    return out, probes


def block_nested_loop(R, S, block_size=3):
    """块嵌套循环:外表按块装入内存,内表只扫 R/block 次。"""
    out, scans = [], 0
    for i in range(0, len(R), block_size):
        block = R[i:i + block_size]
        for s in S:
            scans += 1
            for r in block:
                if r[0] == s[0]:
                    out.append((r, s))
    return out, scans


def hash_join(R, S):
    """基础哈希连接:build 小表 + probe 大表。成本 ~ M + N。"""
    table = {}                      # build 阶段
    for r in R:
        table.setdefault(r[0], []).append(r)
    out, probes = [], 0
    for s in S:                     # probe 阶段
        probes += 1
        for r in table.get(s[0], []):
            out.append((r, s))
    return out, probes, table


def grace_hash_join(R, S, k=4):
    """GRACE 分区哈希连接:两表用同一 h1 分区,逐对分区内存 join。

    讲义:分区阶段 2(M+N) I/O(读写两表),probe 阶段 M+N,
    总成本 3(M+N);单分区仍超内存则换 h2 递归再分区。
    """
    pr, ps = [[] for _ in range(k)], [[] for _ in range(k)]
    for r in R:
        pr[r[0] % k].append(r)      # 分区阶段:不同分区必不匹配
    for s in S:
        ps[s[0] % k].append(s)
    out, joined_partitions = [], 0
    for i in range(k):              # probe 阶段:逐对分区 join
        sub, _, _ = hash_join(pr[i], ps[i])
        out.extend(sub)
        joined_partitions += 1
    return out, (pr, ps), joined_partitions


def sort_merge_join(R, S):
    """排序归并连接:双指针 + 重复键回溯(内表指针回退到重复键起点)。"""
    r_sorted = sorted(R, key=lambda t: t[0])
    s_sorted = sorted(S, key=lambda t: t[0])
    out, i, j, steps = [], 0, 0, 0
    while i < len(r_sorted) and j < len(s_sorted):
        steps += 1
        rk, sk = r_sorted[i][0], s_sorted[j][0]
        if rk < sk:
            i += 1
        elif rk > sk:
            j += 1
        else:                        # 相等:枚举本键的全部组合
            jj = j
            while jj < len(s_sorted) and s_sorted[jj][0] == rk:
                out.append((r_sorted[i], s_sorted[jj]))
                jj += 1
            i += 1                   # 外表前进;下条外键若相同,内表 j 不动
    return out, steps


def sort_cost(pages, B):
    """外部归并排序成本公式(讲义):2P*(1 + ceil(log_{B-1}(P/B)))。"""
    if pages <= B:
        return pages                  # 内存内排序:读一次
    passes = 1 + math.ceil(math.log(math.ceil(pages / B), B - 1))
    return 2 * pages * max(passes, 1)


def main():
    R = [(1, "a"), (2, "b"), (2, "c"), (3, "d"), (5, "e")]      # 外/小表, M 页
    S = [(2, "x"), (2, "y"), (3, "z"), (5, "w"), (7, "q"), (1, "r")]  # 内/大表

    # ---- 1. 五种算法结果一致 ----
    expected = {(r, s) for r, s in hash_join(R, S)[0]}
    assert len(expected) == 7        # 2×2=4 + 3 + 5 + 1
    assert {p for p in simple_nested_loop(R, S)[0]} == expected
    assert {p for p in block_nested_loop(R, S)[0]} == expected
    grace_out, _, parts = grace_hash_join(R, S)
    assert {p for p in grace_out} == expected
    assert {p for p in sort_merge_join(R, S)[0]} == expected
    assert parts == 4                # GRACE:逐对分区都 join 了一遍

    # ---- 2. GRACE 分区只比对同分区 ----
    _, (pr, ps), _ = grace_hash_join(R, S)
    for i in range(len(pr)):
        for r in pr[i]:
            assert r[0] % 4 == i     # 分区 = key % k,两表同一 h1
        assert all(s[0] % 4 == i for s in ps[i])

    # ---- 3. 成本模型对比(讲义数字:M=1000,N=500,B=100)----
    M, m, N, n, B = 1000, 100000, 500, 40000, 100
    snl = M + m * N                  # 朴素嵌套循环:最坏
    bnl = M + (math.ceil(M / (B - 2)) * N)
    hj = 3 * (M + N)                 # 哈希连接(无递归分区)
    smj = (M + N) + (sort_cost(M, B) + sort_cost(N, B))
    assert hj < smj and bnl < smj < snl   # hash 最快;块嵌套(6500)优于 SMJ(7500),均远优于朴素
    assert hj == 4500 and smj == 7500  # 讲义示例:hash 0.45s vs sort-merge 0.75s

    # ---- 4. sort-merge 重复键回溯正确性 ----
    out, steps = sort_merge_join([(9, "r1")], [(9, "s1"), (9, "s2"), (9, "s3")])
    assert len(out) == 3 and steps == 1   # 一个 r 匹配全部重复 s

    # ---- 5. probe 计数:hash join 每条内表元组恰一次探测 ----
    _, probes, table = hash_join(R, S)
    assert probes == len(S)
    assert sum(len(v) for v in table.values()) == len(R)   # build 全量入表

    print("ALL 5 DEMO-3 (JoinAlgorithms) ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
