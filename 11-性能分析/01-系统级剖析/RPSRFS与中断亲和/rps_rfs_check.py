"""RPS / RFS / XPS / flow limit —— 自检（实跑）。

锚点全部来自 docs.kernel.org/networking/scaling.html 的明示例与默认值。
"""

from rps_rfs import (
    FlowLimit,
    format_cpu_mask,
    parse_cpu_mask,
    rfs_decide,
    roundup_pow2,
    rps_flow_cnt_per_queue,
    rps_select_cpu,
    tx_maxrate_enabled,
    xps_may_change_queue,
    xps_select_queue,
)

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"ok   {label} {detail}")
    else:
        _failed += 1
        print(f"FAIL {label} {detail}")


# --- 1. CPU 位图 --------------------------------------------------------------
check("位图 0000000f", parse_cpu_mask("0000000f") == [0, 1, 2, 3], f"{parse_cpu_mask('0000000f')}")
check("位图高位字", parse_cpu_mask("f0000000") == [28, 29, 30, 31], f"{parse_cpu_mask('f0000000')}")
check("多字最低字在前", parse_cpu_mask("00000000,00000001") == [32], f"{parse_cpu_mask('00000000,00000001')}")
check("0 表示空（RPS 禁用）", parse_cpu_mask("0") == [], "")
check("位图往返", parse_cpu_mask(format_cpu_mask([0, 5, 32])) == [0, 5, 32],
      format_cpu_mask([0, 5, 32]))

# --- 2. 条目数取整 ------------------------------------------------------------
check("65536 已是 2 的幂", roundup_pow2(65536) == 65536, "")
check("60000 向上取整到 65536", roundup_pow2(60000) == 65536, f"{roundup_pow2(60000)}")
check("文档示例 131072/16=8192", rps_flow_cnt_per_queue(131072, 16) == 8192,
      f"{rps_flow_cnt_per_queue(131072, 16)}")

# --- 3. RPS 选 CPU ------------------------------------------------------------
cpus = [0, 1, 2, 3]
check("hash 取模选 CPU", rps_select_cpu(10, cpus, irq_cpu=0) == 2, f"10 % 4 = {10 % 4}")
check("hash 0 选首个", rps_select_cpu(0, cpus, irq_cpu=0) == 0, "")
check("rps_cpus=0 ⇒ 留在中断 CPU", rps_select_cpu(10, [], irq_cpu=7) == 7, "RPS 禁用")
check("非 2 的幂长度的列表也按列表长度取模",
      rps_select_cpu(7, [0, 1, 2], irq_cpu=0) == 1, f"7 % 3 = {7 % 3}")

# --- 4. RFS 三条判据 ----------------------------------------------------------
check("desired == current ⇒ 不切", rfs_decide(3, 3, 0, 999, 8) == 3, "")
check("旧 CPU 已排空 ⇒ 切", rfs_decide(5, 1, 100, 100, 8) == 5, "head 100 >= tail 100")
check("旧 CPU 还有残留 ⇒ 不切（防乱序）", rfs_decide(5, 1, 99, 100, 8) == 1, "head 99 < tail 100")
check("current 未设置 ⇒ 无条件切", rfs_decide(5, 9, 0, 100, 8) == 5, "9 >= nr_cpu_ids 8")
check("current 下线 ⇒ 切", rfs_decide(5, 2, 0, 100, 8, offline_cpus=[2]) == 5, "")
check("在线且有残留 ⇒ 仍不切", rfs_decide(5, 2, 0, 100, 8, offline_cpus=[3]) == 2, "")

# --- 5. flow limit ------------------------------------------------------------
fl = FlowLimit(netdev_max_backlog=1000)
check("阈值是 max_backlog 的一半", fl.threshold() == 500.0, f"{fl.threshold()}")
check("队列 400 < 500 ⇒ 不激活", fl.active(400) is False, "")
check("队列 501 > 500 ⇒ 激活", fl.active(501) is True, "")

# 构造：256 的历史里 129 个属于 flow 7（过半），其余是别的流
fl2 = FlowLimit(netdev_max_backlog=1000)
fl2.history.extend([7] * 129 + [9] * 127)
check("历史窗口长度被截到 256", len(fl2.history) == 256, f"{len(fl2.history)}")
share = list(fl2.history).count(7) / 256
check("占比 129/256 刚过半", share > 0.5, f"{share:.6f}")
check("过半的大流被丢", fl2.should_drop(7, 800) is True, "")
check("被丢的流不再入历史", len(fl2.history) == 256, "丢弃不入队")
fl3 = FlowLimit(netdev_max_backlog=1000)
fl3.history.extend([7] * 128 + [9] * 128)
check("恰好一半不丢（严格大于）", fl3.should_drop(7, 800) is False, "ratio 默认是 half")
check("未激活时一律不丢", FlowLimit().should_drop(1, 10) is False, "队列远未到阈值")
check("默认哈希表 4096 桶", FlowLimit().table_len == 4096, "")

# --- 6. XPS -------------------------------------------------------------------
cmap = {0: [0], 1: [1], 2: [2, 3], 3: [2, 3]}
check("CPU→单队列", xps_select_queue(cmap, 0) == 0, "")
check("CPU→多队列用 flow hash", xps_select_queue(cmap, 2, flow_hash=1) == 3, f"1 % 2 = 1 → 队列 3")
check("hash 0 取首个候选", xps_select_queue(cmap, 2, flow_hash=0) == 2, "")
check("未命中映射 ⇒ None", xps_select_queue(cmap, 9) is None, "")
check("ooo_okay 才允许改队列", xps_may_change_queue(True) and not xps_may_change_queue(False), "")
check("tx_maxrate 默认 0 不限速", tx_maxrate_enabled(0) is False, "")
check("tx_maxrate > 0 限速", tx_maxrate_enabled(500) is True, "")

print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
