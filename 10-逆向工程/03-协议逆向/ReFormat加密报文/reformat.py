#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ReFormat（ESORICS 2009）最小复现：相位划分 + 数据生命周期 → 定位解密后的明文缓冲区。

来源：Z. Wang, X. Jiang, W. Cui, X. Wang, M. Grace, *ReFormat: Automatic Reverse
Engineering of Encrypted Messages*, ESORICS 2009（LNCS 5789, pp. 200-215）。

复现内容：
  §3.1/§3.3 相位划分器 Phase Profiler（两步：累计百分比定搜索范围 → 片段级百分比定跃迁点）
  §3.4      数据生命周期分析 Data Lifetime Analyzer（write set ∩ read set）
  Table 1   解密算法与明文处理程序的「算术 + 位运算指令占比」实测对照
"""

# Table 1（论文 §3.1 实测）：(名字, 算术与位运算指令数, 指令总数)
TABLE1 = [
    ("DES", 68921, 69112), ("CAST", 18917, 21225), ("RC4", 2709, 3042),
    ("AES", 6892, 8475),
    ("HTTP request", 429, 3227), ("FTP port", 421, 5898), ("DNS response", 223, 1687),
    ("RPC bind", 186, 2342), ("JPEG", 1112, 12898), ("BMP", 229, 956),
]
DECRYPT_NAMES = {"DES", "CAST", "RC4", "AES"}
THRESHOLD = 50.0          # §3.3：阈值取 50%（正文脚注：25%~80% 之间的任何值效果相同）


def pct(ab, total):
    return ab * 100.0 / total if total else 0.0


# --------------------------------------------- §3.3 Step I 累计百分比
def cumulative_pct(itrace):
    """itrace: [(func, stack_frame_id, is_arith_or_bitwise)]。

    第 n 条指令处的累计百分比 = 前 n 条指令中算术与位运算指令的占比。
    """
    out, cnt = [], 0
    for i, (_, _, ab) in enumerate(itrace):
        if ab:
            cnt += 1
        out.append(pct(cnt, i + 1))
    return out


# --------------------------------------------- §3.3 Step II 函数片段
def function_fragments(itrace):
    """一个 function fragment = 连续、属于同一函数、且在同一运行期栈帧下执行的指令。

    父函数 A 调用 B 再返回，会切出 FA1 / FB / FA2 三个片段；
    每条指令**有且仅有一个**所属片段。
    """
    frags = []
    for i, (func, sid, _) in enumerate(itrace):
        if frags and frags[-1]["func"] == func and frags[-1]["sid"] == sid:
            frags[-1]["end"] = i + 1
        else:
            frags.append({"start": i, "end": i + 1, "func": func, "sid": sid})
    for f in frags:
        ab = sum(1 for i in range(f["start"], f["end"]) if itrace[i][2])
        f["pct"] = pct(ab, f["end"] - f["start"])
    return frags


def phase_profile(itrace, threshold=THRESHOLD):
    """返回 (transition_fragment, imax, imin)；跃迁点 = 该片段最后一条指令。"""
    cum = cumulative_pct(itrace)
    imax = cum.index(max(cum))
    imin = cum.index(min(cum))
    lo, hi = min(imax, imin), max(imax, imin)
    trans = None
    for f in function_fragments(itrace):
        if f["end"] <= lo or f["start"] > hi:
            continue
        if f["pct"] >= threshold:
            trans = f                      # 取**最后一个**超过阈值的片段
    return trans, imax, imin


# ------------------------------------- §3.4 数据生命周期分析
def data_lifetime(ops, transition):
    """ops: [(idx, op, buf)]，op ∈ {"alloc", "write", "read", "free"}。

    返回 (write_set, read_set, answer)；answer 已按**首次读取的时序**排序。

    论文 §3.4 的活性定义：
      · 启动时，为全局变量预分配的缓冲区标记为 live；
      · 解密阶段：在堆/栈上分配 → live；被释放 → 失效；
      · 进入正常协议处理阶段后规则改变：缓冲区**被释放或被访问（读或写）即失效**
        （写会让它不再持有解密阶段的内容；读只关心**第一次**读）。
    """
    live = {}
    written = set()
    for idx, op, buf in ops:
        if idx > transition:
            break
        if op == "alloc":
            live[buf] = True
        elif op == "free":
            live[buf] = False
        elif op == "write":
            written.add(buf)
    write_set = {b for b in written if live.get(b, False)}

    read_set, order = set(), []
    for idx, op, buf in ops:
        if idx <= transition:
            continue
        if op == "alloc":
            live[buf] = True
        elif op == "free":
            live[buf] = False
        elif op in ("read", "write"):
            if op == "read" and live.get(buf, False) and buf not in read_set:
                read_set.add(buf)
                order.append(buf)
            live[buf] = False            # 处理阶段：一旦被访问即失效
    answer = [b for b in order if b in write_set]
    return write_set, read_set, answer
