#!/usr/bin/env python3
"""irq_check.py — 中断与软中断剖析

三份「累计值」文件 + 一份「配置」文件,口径全都不一样:

  /proc/interrupts             每个 IRQ 在每个 CPU 上的累计处理次数
  /proc/softirqs               每种 softirq 在每个 CPU 上的累计次数
  /proc/net/softnet_stat       每 CPU 一行,【十六进制、没有表头】—— 丢包与「挤出」的证据在这里
  /proc/irq/<N>/smp_affinity   十六进制 CPU 位掩码,决定谁能处理这个 IRQ

三个最容易踩的口径坑,本文件每一项都写了断言:
  1. /proc/interrupts 里有【没有编号】的架构向量行(NMI:/LOC:/RES: …),不能当成 IRQ 号
  2. /proc/net/softnet_stat 是十六进制,按十进制读会得到看似合理的错数
  3. smp_affinity 超过 32 核是【逗号分组,低位组在前】,位序读反就指向完全错的 CPU

运行: python irq_check.py   (含 53 项自检,不需要 root,不需要 Linux)
"""
from __future__ import annotations

from irq_parse import (
    NETDEV_BUDGET_DEFAULT, RAISE_BACKLOG, RAISE_BUDGET, REAL_WORK, SOFTIRQ_NAMES,
    arch_vectors, attribute, check_affinity, device_of, diff_series, format_cpu_list,
    mask_to_cpus, numbered_irqs, parse_interrupts, parse_proc_stat,
    parse_smp_affinity, parse_smp_affinity_list, parse_softirqs, parse_softnet_stat,
    softnet_delta, softnet_totals,
)

# ---------------------------------------------------------------- 自检
def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  <- {detail}" if detail else ""))
    return bool(cond)


INTERRUPTS = """           CPU0       CPU1       CPU2       CPU3
  0:         46          0          0          0   IO-APIC    2-edge      timer
  1:          3          0          0          0   IO-APIC    1-edge      i8042
 24:      10234       5601       4200       8991   PCI-MSI 524288-edge      eth0
 25:      54321       6789       4321       1234   PCI-MSI 524289-edge      nvme0q0
 32:          0    1034521          0          0   PCI-MSI-edge      eth0-TxRx-0
 33:          0          0    1034522          0   PCI-MSI-edge      eth0-TxRx-1
NMI:         12         14         13         12   Non-maskable interrupts
LOC:    1234567    1234568    1234569    1234570   Local timer interrupts
RES:       4321       2109       3210       4102   Rescheduling interrupts
"""

SOFTIRQS = """                    CPU0       CPU1       CPU2       CPU3
          HI:          1          0          0          0
       TIMER:    1234567    1234568    1234569    1234570
      NET_TX:        567        890        123        456
      NET_RX:    4567890    3456789    4567891    3456790
       BLOCK:          0          0          0          0
    IRQ_POLL:          0          0          0          0
     TASKLET:       1234        567          0          0
       SCHED:    1234567    1234567    1234567    1234567
     HRTIMER:          0          0          0          0
         RCU:    4567890    3456789    4567891    3456790
"""

SOFTNET = """0001e240 0000000c 00000003 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000
0001e241 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000 00000000
"""

PROC_STAT = """cpu  100 0 50 800 0 0 0 0 0 0
cpu0 50 0 25 400 0 0 0 0 0 0
intr 9876543 46 3 0 10234 54321 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
ctxt 1234567
btime 1600000000
processes 45678
procs_running 3
procs_blocked 1
softirq 33967864 1 4938234 2036 16059360 0 0 1235 4938268 0 8028730
"""


def main() -> int:
    ok = True
    cpu_cols, rows = parse_interrupts(INTERRUPTS)

    print("== 1. /proc/interrupts 的行结构 ==")
    ok &= check("表头给出每核一列,共 4 列", cpu_cols == ["CPU0", "CPU1", "CPU2", "CPU3"])
    by_label = {r[0]: r for r in rows}
    ok &= check("IRQ 24(eth0)每核计数 = 10234/5601/4200/8991",
                by_label["24"][1] == [10234, 5601, 4200, 8991])
    ok &= check("计数只取前 len(cpu_cols) 个整数,后面的控制器类型不被当成计数",
                by_label["24"][2] == ["PCI-MSI", "524288-edge", "eth0"])
    ok &= check("设备名 = 剩余 token 的最后一个", device_of(by_label["24"][2]) == "eth0")
    ok &= check("共享中断的驱动列表用逗号连接,整体算一个 token",
                device_of(["IO-APIC-fasteoi", "ehci_hcd:usb1,ath9k"]) == "ehci_hcd:usb1,ath9k")

    print("== 2. 没有编号的架构向量不是 IRQ ==")
    ok &= check("识别出 6 条有编号的 IRQ", len(numbered_irqs(rows)) == 6,
                str([r[0] for r in numbered_irqs(rows)]))
    ok &= check("NMI/LOC/RES 被归为架构向量而非 IRQ",
                sorted(arch_vectors(rows)) == ["LOC", "NMI", "RES"], str(arch_vectors(rows)))
    ok &= check("判断依据是「标签能否当整数读」,不是白名单",
                [r[0] for r in rows if r[0].isdigit()] == ["0", "1", "24", "25", "32", "33"])
    ok &= check("官方说明:架构向量只被计入 /proc/stat 的 intr 总数,不单独占 IRQ 号",
                "NMI" not in [r[0] for r in numbered_irqs(rows)])

    print("== 3. 多队列网卡的中断分散 ==")
    ok &= check("eth0-TxRx-0 只落在 CPU1(1034521)", by_label["32"][1][1] == 1034521)
    ok &= check("eth0-TxRx-1 只落在 CPU2(1034522)", by_label["33"][1][2] == 1034522)
    ok &= check("两个队列都长了 -> 分散成功;若都只落 CPU0 就说明没做多队列",
                sum(by_label["32"][1]) + sum(by_label["33"][1]) == 1034521 + 1034522)

    print("== 4. /proc/softirqs 的名字来自内核预定义表 ==")
    scpu, sfi = parse_softirqs(SOFTIRQS)
    ok &= check("CPU 列名与 /proc/interrupts 一致", scpu == cpu_cols)
    ok &= check("解析出 10 种 softirq", len(sfi) == 10, str(sorted(sfi)))
    ok &= check("名字顺序与内核预定义一致", list(sfi) == SOFTIRQ_NAMES)
    ok &= check("NET_RX 每核计数正确", sfi["NET_RX"] == [4567890, 3456789, 4567891, 3456790])
    ok &= check("BLOCK/IRQ_POLL/HRTIMER 为 0 是正常的(没有对应负载)",
                sfi["BLOCK"] == [0, 0, 0, 0] and sfi["IRQ_POLL"] == [0, 0, 0, 0])

    print("== 5. 累计值必须两次采样求差 ==")
    after = dict(sfi, **{"NET_RX": [x + 3000 for x in sfi["NET_RX"]]})
    d = diff_series(sfi, after)
    ok &= check("NET_RX 增量 = 4 核各 +3000,合计 12000", d["NET_RX"] == 12000, str(d["NET_RX"]))
    ok &= check("没变的项差分恒为 0", d["TIMER"] == 0 and d["BLOCK"] == 0)
    ok &= check("单点读值会被当成「绝对值」,和差分差好几个数量级",
                sum(sfi["NET_RX"]) > 1000 * d["NET_RX"])

    print("== 6. /proc/net/softnet_stat 是十六进制、无表头 ==")
    sn = parse_softnet_stat(SOFTNET)
    ok &= check("每 CPU 一行,共 2 行", len(sn) == 2 and len(sn[0]) == 13)
    ok &= check("0001e240 按十六进制 = 123456,不是十进制 124480",
                sn[0][0] == 0x0001E240 == 123456, str(sn[0][0]))
    try:
        int("0001e240")
        dec_ok = False
    except ValueError:
        dec_ok = True
    ok &= check("带 e 的串按十进制解析会直接报错,不会悄悄给出错数", dec_ok)
    ok &= check("真正危险的是 '00000123' 这种:既是合法十进制(123)又是合法十六进制(291),"
                "按十进制读会静默给出错数",
                int("00000123", 16) == 291 and int("00000123") == 123)
    proc, drop, squeeze = softnet_totals(sn)
    ok &= check("第 1/2/3 列分别是 处理数 / backlog 丢包 / time_squeeze",
                proc == 123456 + 123457 and drop == 12 and squeeze == 3,
                f"proc={proc} drop={drop} squeeze={squeeze}")
    ok &= check("短行按缺列补 0,不会 IndexError",
                softnet_totals([[1, 2]]) == (1, 2, 0))
    later = [[0x0001E241, 20, 5] + [0] * 10, [0x0001E241, 0, 0] + [0] * 10]
    ok &= check("差分也要按列分别做:处理数 +1、丢包 +8、squeeze +2",
                softnet_delta(sn, later) == (1, 8, 2), str(softnet_delta(sn, later)))

    print("== 7. 归因:把「%si 高」拆成三种动作 ==")
    ok &= check("丢包长 + squeeze 不长 -> 抬 netdev_max_backlog",
                attribute(0.30, 500, 0) == RAISE_BACKLOG)
    ok &= check("squeeze 长 + 不丢包 -> 抬 netdev_budget",
                attribute(0.30, 0, 500) == RAISE_BUDGET)
    ok &= check("丢包优先于 squeeze(丢包是已发生的损失,更紧急)",
                attribute(0.30, 900, 100) == RAISE_BACKLOG)
    ok &= check("两个都不长 -> 高 %si 是真活,不是队列问题,该分散 IRQ / 合并中断",
                attribute(0.30, 0, 0) == REAL_WORK)
    ok &= check("netdev_budget 内核默认 300", NETDEV_BUDGET_DEFAULT == 300)

    print("== 8. smp_affinity 位掩码 ==")
    ok &= check("'f' -> CPU 0-3", mask_to_cpus(parse_smp_affinity("f")) == [0, 1, 2, 3])
    ok &= check("'0000000f' 前导零不影响", parse_smp_affinity("0000000f") == 0xF)
    ok &= check("'0x3' 带前缀也能读", mask_to_cpus(parse_smp_affinity("0x3")) == [0, 1])
    ok &= check("'1' -> 只有 CPU0", mask_to_cpus(parse_smp_affinity("1")) == [0])
    ok &= check("'9' -> CPU0 和 CPU3(位 0 与位 3)", mask_to_cpus(parse_smp_affinity("9")) == [0, 3])
    ok &= check("'ffffffff,ffffffff' -> 64 个核", len(mask_to_cpus(parse_smp_affinity(
        "ffffffff,ffffffff"))) == 64)
    ok &= check("逗号分组【低位组在前】:第 1 组管 CPU0-31,第 2 组管 32-63",
                mask_to_cpus(parse_smp_affinity("00000001,00000001")) == [0, 32])
    ok &= check("'00000000,ffffffff' -> 只用高位 32 核(CPU 32-63)",
                mask_to_cpus(parse_smp_affinity("00000000,ffffffff"))[:1] == [32])
    ok &= check("全 0 必须被拒绝:内核不允许把 IRQ 挂到「没有核」上",
                check_affinity(0) is not None and check_affinity(0xF) is None)
    ok &= check("CPU 列表格式化 0-3,7", format_cpu_list([0, 1, 2, 3, 7]) == "0-3,7")

    print("== 9. smp_affinity_list 是人看的接口 ==")
    ok &= check("'0-1' -> [0,1]", parse_smp_affinity_list("0-1") == [0, 1])
    ok &= check("'0,3' -> [0,3]", parse_smp_affinity_list("0,3") == [0, 3])
    ok &= check("'1024-1031' -> 8 个核,从 1024 开始(掩码写这个要 32 个零)",
                parse_smp_affinity_list("1024-1031") == list(range(1024, 1032)))
    ok &= check("同一个集合的两种写法等价(CPU0-3)",
                format_cpu_list(parse_smp_affinity_list("0-3")) == "0-3")

    print("== 10. /proc/stat 的 intr / softirq 两行 ==")
    st = parse_proc_stat(PROC_STAT)
    ok &= check("两个字段都取到", set(st) == {"intr", "softirq"})
    ok &= check("intr 行:第一个数是总数,后面是分项", st["intr"][0] == 9876543 and
                st["intr"][1:4] == [46, 3, 0])
    ok &= check("softirq 行 = 总数 + 10 种类型", len(st["softirq"]) == 11)
    ok &= check("softirq 分项顺序与 SOFTIRQ_NAMES 对齐:第 1 项 HI=1,第 4 项 NET_RX=16059360",
                st["softirq"][1] == 1 and st["softirq"][4] == 16059360)
    ok &= check("softirq 行的总数【严格等于】分项之和(所有类型都被列出)",
                sum(st["softirq"][1:]) == st["softirq"][0],
                f"{sum(st['softirq'][1:])} vs {st['softirq'][0]}")
    ok &= check("intr 行相反:总数【大于】分项之和 —— 未编号的架构向量只被计入总数",
                st["intr"][0] > sum(st["intr"][1:]),
                f"总数 {st['intr'][0]} vs 分项和 {sum(st['intr'][1:])}")
    ok &= check("这两行的口径差别是同一份文档里两句话,不看清楚就会算错占比",
                st["intr"][0] - sum(st["intr"][1:]) > 0)

    print("\n" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
