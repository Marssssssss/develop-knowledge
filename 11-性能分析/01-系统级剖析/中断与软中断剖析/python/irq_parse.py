#!/usr/bin/env python3
"""irq_parse.py — /proc 里「中断相关文件」的解析器(纯函数,无副作用,不含自检)

  /proc/interrupts             parse_interrupts / numbered_irqs / arch_vectors / device_of
  /proc/softirqs               parse_softirqs / diff_series
  /proc/net/softnet_stat       parse_softnet_stat / softnet_totals / softnet_delta
  /proc/irq/<N>/smp_affinity   parse_smp_affinity / mask_to_cpus / parse_smp_affinity_list
  /proc/stat                   parse_proc_stat

拆出来只是为了让单文件落到 300 行以内;全部断言与自检在 irq_check.py。
"""
from __future__ import annotations

# softirq 类型(kernel 预定义,顺序与 /proc/softirqs、/proc/stat 的 softirq 行一致)
SOFTIRQ_NAMES = ["HI", "TIMER", "NET_TX", "NET_RX", "BLOCK",
                 "IRQ_POLL", "TASKLET", "SCHED", "HRTIMER", "RCU"]

# net.core.netdev_budget 的内核默认值
NETDEV_BUDGET_DEFAULT = 300

# 归因结论
REAL_WORK = "REAL_WORK"              # 高 %si 是真活,不是排队问题
RAISE_BACKLOG = "RAISE_BACKLOG"      # 队列太浅,包被丢在 backlog 里
RAISE_BUDGET = "RAISE_BUDGET"        # 单次预算用完还有活,被 time_squeeze 打断


# ---------------------------------------------------------------- /proc/interrupts
def parse_interrupts(text: str) -> tuple[list[str], list[tuple[str, list[int], list[str]]]]:
    """返回 (CPU 列名, [(标签, 每核计数, 剩余 token)])。

    每条数据的第一个 token 是「标签 + 冒号」:有编号的是 IRQ 号(如 '24:'),
    没有编号的是架构向量(NMI: / LOC: / RES: …)。后者不是 IRQ,不能被当成中断号。
    紧跟标签的前 len(cpu_cols) 个整数才是每核计数,再往后的 token 是控制器类型
    与注册到该中断的驱动名(逗号分隔,一个 IRQ 可以共享给多个驱动)。
    """
    rows: list[tuple[str, list[int], list[str]]] = []
    cpu_cols: list[str] = []
    for line in text.splitlines():
        tok = line.split()
        if not tok:
            continue
        if not cpu_cols and all(t.startswith("CPU") for t in tok):
            cpu_cols = tok
            continue
        if not tok[0].endswith(":"):
            continue
        label = tok[0][:-1]
        counts, n = [], 0
        while n < len(cpu_cols):
            try:
                counts.append(int(tok[1 + n]))
            except (IndexError, ValueError):
                break
            n += 1
        rows.append((label, counts, tok[1 + n:]))
    return cpu_cols, rows


def numbered_irqs(rows) -> list[tuple[str, list[int], list[str]]]:
    """只留下有编号的行 —— 判断「是不是 IRQ」的唯一依据是标签能不能当整数读。"""
    return [r for r in rows if r[0].isdigit()]


def arch_vectors(rows) -> list[str]:
    """架构向量行(NMI / LOC / RES / CAL / TLB …),它们在 /proc/stat 里只被计入总数。"""
    return [r[0] for r in rows if not r[0].isdigit()]


def device_of(rest: list[str]) -> str:
    """最后一个 token 是驱动/设备名(可能是 'a,b' 这种共享中断的驱动列表)。"""
    return rest[-1] if rest else ""


# ---------------------------------------------------------------- /proc/softirqs
def parse_softirqs(text: str) -> tuple[list[str], dict[str, list[int]]]:
    """cpu 列名 + {softirq 名: 每核累计次数}。名字从内核预定义表里取,不靠行内猜。"""
    cpu_cols: list[str] = []
    out: dict[str, list[int]] = {}
    for line in text.splitlines():
        tok = line.replace(":", " ").split()
        if not tok:
            continue
        if not cpu_cols and all(t.startswith("CPU") for t in tok):
            cpu_cols = tok
            continue
        name = tok[0]
        try:
            out[name] = [int(x) for x in tok[1:1 + len(cpu_cols)]]
        except ValueError:
            continue
    return cpu_cols, out


def diff_series(before: dict[str, list[int]], after: dict[str, list[int]]) -> dict[str, int]:
    """累计值只能求差。求和到「全系统总量」再差分,避免逐核相加时的错位。"""
    return {k: sum(after.get(k, [])) - sum(before.get(k, [])) for k in set(before) | set(after)}


# ---------------------------------------------------------------- /proc/net/softnet_stat
def parse_softnet_stat(text: str) -> list[list[int]]:
    """每 CPU 一行,【全部是十六进制】【文件里没有表头】。

    列序必须靠外部知识:1=处理过的包数 2=因 backlog 满而丢掉的包数
    3=time_squeeze(预算用完但还有活)。后续列随内核版本增加,含义不稳定。
    """
    out = []
    for line in text.splitlines():
        tok = line.split()
        if tok:
            out.append([int(x, 16) for x in tok])
    return out


def softnet_totals(rows: list[list[int]]) -> tuple[int, int, int]:
    """(处理总数, 丢包总数, squeeze 总数)。短行按缺列补 0。"""
    padded = [r + [0] * (3 - len(r)) for r in rows]
    return (sum(r[0] for r in padded), sum(r[1] for r in padded), sum(r[2] for r in padded))


def softnet_delta(a: list[list[int]], b: list[list[int]]) -> tuple[int, int, int]:
    ta, da, sa = softnet_totals(a)
    tb, db, sb = softnet_totals(b)
    return tb - ta, db - da, sb - sa


def attribute(si_share: float, dropped: int, squeeze: int) -> str:
    """把「%si 高」拆成三种完全不同的动作。判定顺序:dropped 优先于 squeeze,
    因为丢包是已经发生的损失,而 squeeze 只是「被打断」,前者更紧急。"""
    if dropped <= 0 and squeeze <= 0:
        return REAL_WORK
    return RAISE_BACKLOG if dropped > squeeze else RAISE_BUDGET


# ---------------------------------------------------------------- IRQ 亲和性
def parse_smp_affinity(text: str) -> int:
    """十六进制位掩码 -> 整数。支持 'f' / '0x3' / 'ffffffff,ffffffff' 三种写法。

    逗号分组时【低位组在前】:第一个组代表 CPU 0..31,第二个组代表 32..63。
    写成 0xffffffff,00000000 表示「只用低 32 核」,顺序读反就指向完全错的 CPU。
    """
    s = text.strip().lower().replace("0x", "")
    if not s:
        raise ValueError("空的 affinity")
    groups = [g.strip() for g in s.split(",")]
    mask = 0
    for i, g in enumerate(groups):
        mask |= int(g, 16) << (32 * i)
    return mask


def mask_to_cpus(mask: int) -> list[int]:
    return [i for i in range(mask.bit_length()) if mask >> i & 1]


def format_cpu_list(cpus: list[int]) -> str:
    """[0,1,2,3,7] -> '0-3,7'。"""
    out, i = [], 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        out.append(str(cpus[i]) if j == i else f"{cpus[i]}-{cpus[j]}")
        i = j + 1
    return ",".join(out)


def parse_smp_affinity_list(text: str) -> list[int]:
    """'0-1' / '0,3' / '1024-1031' -> CPU 列表。这是给人看的接口,避免掩码要写 32 个零。"""
    out: list[int] = []
    for part in text.strip().split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return sorted(out)


def check_affinity(mask: int) -> str | None:
    """内核不允许把所有 CPU 都关掉。返回错误说明,合法则返回 None。"""
    if mask == 0:
        return "不能把 affinity 设成全 0 —— 内核会拒绝,这个 IRQ 会无核可用"
    return None


# ---------------------------------------------------------------- /proc/stat
def parse_proc_stat(text: str) -> dict[str, list[int]]:
    """取出 intr 与 softirq 两行。两行结构一样:第一个数是总数,后面是分项。

    intr 行的分项是 IRQ 号(含架构向量),softirq 行的分项按 SOFTIRQ_NAMES 的顺序。
    """
    out: dict[str, list[int]] = {}
    for line in text.splitlines():
        tok = line.split()
        if tok and tok[0] in ("intr", "softirq"):
            out[tok[0]] = [int(x) for x in tok[1:]]
    return out


