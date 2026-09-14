#!/usr/bin/env python3
"""smaps_pss.py — 解析 /proc/[pid]/smaps,算 RSS / PSS / USS 并演示 PSS 均摊

为什么必须懂这三种口径:
  两个进程共享一个 4 MB 的库时,RSS 会在【每个】进程里都记 4 MB(求和翻倍),
  PSS 则把每一页按共享进程数均摊,求和才等于真实占用。

权威依据:docs.kernel.org/filesystems/proc.html
  · PSS 定义:"每页都按其被多少个进程共享来均分";1000 页独享 + 1000 页与另一进程共享
    => PSS = 1500
  · 私有/共享的判定与 MAP_SHARED 无关:一页恰好被映射一次=私有,被映射多次=共享
    (同一进程内多次映射也算)
  · THP/大分配下语义略有不同,PSS 可能用平均映射次数近似 => 可能不精确
  · Shared_Hugetlb / Private_Hugetlb 出于历史原因不计入 Rss/Pss
  · smaps_rollup 是全进程汇总,并额外提供 Pss_Anon / Pss_File / Pss_Shmem

用法:
    python3 smaps_pss.py                    # 解析内置样例(两个进程共享一个库)
    python3 smaps_pss.py /proc/self/smaps   # 解析真实 smaps
"""

import re
import sys

# 内置样例:进程 A / 进程 B 各自有私有匿名内存,并共享一个 4096 kB 的 libfoo.so
# 单位 kB;字段取值刻意贴合内核输出(共享映射的 Private_* 为 0,共享 40000 kB 场景)
SAMPLE = {
    "pid_A": """7f8a00000000-7f8a00400000 r-xp 00000000 08:01 131075 /usr/lib/libfoo.so
Size:               4096 kB
Rss:                4096 kB
Pss:                2048 kB
Shared_Clean:       4096 kB
Shared_Dirty:          0 kB
Private_Clean:         0 kB
Private_Dirty:         0 kB
Referenced:         4096 kB
Anonymous:             0 kB
AnonHugePages:         0 kB
Swap:                  0 kB
7f8a10000000-7f8a10a00000 rw-p 00000000 00:00 0 [heap]
Size:              10240 kB
Rss:                8192 kB
Pss:                8192 kB
Shared_Clean:          0 kB
Shared_Dirty:          0 kB
Private_Clean:         0 kB
Private_Dirty:      8192 kB
Referenced:         8192 kB
Anonymous:          8192 kB
AnonHugePages:      2048 kB
Swap:                  0 kB
""",
    "pid_B": """7f8a00000000-7f8a00400000 r-xp 00000000 08:01 131075 /usr/lib/libfoo.so
Size:               4096 kB
Rss:                4096 kB
Pss:                2048 kB
Shared_Clean:       4096 kB
Shared_Dirty:          0 kB
Private_Clean:         0 kB
Private_Dirty:         0 kB
Referenced:         4096 kB
Anonymous:             0 kB
AnonHugePages:         0 kB
Swap:                  0 kB
7f9b20000000-7f9b20300000 rw-p 00000000 00:00 0 [heap]
Size:               3072 kB
Rss:                3072 kB
Pss:                3072 kB
Shared_Clean:          0 kB
Shared_Dirty:          0 kB
Private_Clean:         0 kB
Private_Dirty:      3072 kB
Referenced:         3072 kB
Anonymous:          3072 kB
AnonHugePages:         0 kB
Swap:                  0 kB
""",
}

FIELDS = ("Size", "Rss", "Pss", "Shared_Clean", "Shared_Dirty", "Private_Clean",
          "Private_Dirty", "Referenced", "Anonymous", "AnonHugePages", "Swap")
VMA_RE = re.compile(r"^([0-9a-f]+)-([0-9a-f]+)\s+(\S{4})\s+\S+\s+\S+\s+\S+\s*(.*)$")
FIELD_RE = re.compile(r"^(\w+):\s+(\d+)\s+kB")


def parse_smaps(text: str):
    """返回 [ {vma 信息 + 字段值(kB)}, ... ]"""
    vmas, cur = [], None
    for line in text.splitlines():
        m = VMA_RE.match(line)
        if m and not line.startswith(" "):
            if cur:
                vmas.append(cur)
            cur = {"start": m.group(1), "end": m.group(2), "perm": m.group(3),
                   "path": m.group(4) or "[anon]", **{f: 0 for f in FIELDS}}
            continue
        if cur is None:
            continue
        f = FIELD_RE.match(line.strip())
        if f and f.group(1) in FIELDS:
            cur[f.group(1)] = int(f.group(2))
    if cur:
        vmas.append(cur)
    return vmas


def summarize(name: str, vmas: list):
    rss = sum(v["Rss"] for v in vmas)
    pss = sum(v["Pss"] for v in vmas)
    uss = sum(v["Private_Clean"] + v["Private_Dirty"] for v in vmas)
    thp = sum(v["AnonHugePages"] for v in vmas)
    print(f"\n--- {name} ---")
    print(f"  {'VMA / 文件':<34}{'Rss':>10}{'Pss':>10}{'Private':>10}{'AnonHuge':>10}")
    for v in vmas:
        priv = v["Private_Clean"] + v["Private_Dirty"]
        label = f"{v['start'][-5:]}..{v['path'][:24]}"
        print(f"  {label:<34}{v['Rss']:>10}{v['Pss']:>10}{priv:>10}{v['AnonHugePages']:>10}")
    print(f"  {'合计(kB)':<34}{rss:>10}{pss:>10}{uss:>10}{thp:>10}")
    print(f"  RSS={rss} kB  PSS={pss} kB  USS(私有)={uss} kB  "
          f"PSS 比 RSS 少 {rss - pss} kB(被均摊掉的共享页)")
    return dict(rss=rss, pss=pss, uss=uss, thp=thp, vmas=vmas)


def demo_pss_math():
    """用内核文档里的例子验证均摊算术"""
    print("\n=== PSS 均摊算术(内核文档给的例子)===")
    private_pages, shared_pages, sharers = 1000, 1000, 2
    pss_pages = private_pages + shared_pages / sharers
    print(f"  进程独享 {private_pages} 页 + 与另一个进程共享 {shared_pages} 页")
    print(f"  RSS = {private_pages + shared_pages} 页(把共享页整份算给自己)")
    print(f"  PSS = {private_pages} + {shared_pages}/{sharers} = "
          f"{pss_pages:.0f} 页  <- 与内核文档的 1500 一致 = {pss_pages == 1500}")


def demo_shared_lib():
    """两个进程共享同一个库时,只有 PSS 求和才收敛"""
    print("\n=== 跨进程求和:为什么 RSS 会翻倍 ===")
    a = summarize("进程 A", parse_smaps(SAMPLE["pid_A"]))
    b = summarize("进程 B", parse_smaps(SAMPLE["pid_B"]))
    lib_rss = sum(v["Rss"] for v in a["vmas"] if "libfoo" in v["path"])
    print(f"\n  共享库真实占用 = {lib_rss} kB(物理页只有一份)")
    print(f"  Σ RSS = {a['rss'] + b['rss']} kB  -> 把库算了两次,高估 {lib_rss} kB")
    print(f"  Σ PSS = {a['pss'] + b['pss']} kB  -> 库被均摊成 {lib_rss // 2} kB × 2 = {lib_rss} kB,正确")
    print(f"  Σ USS = {a['uss'] + b['uss']} kB  -> 只算私有部分,漏掉共享的库")
    print("  结论:算\"一组进程总共占多少内存\"用 PSS;算\"杀它能回收多少\"用 USS。")
    print(f"\n  样例里 A 的 AnonHugePages = {a['thp']} kB:THP 场景下内核可能用大分配内每页的")
    print("  平均映射次数近似,故 PSS 可能不精确(proc.rst 明文提示)。")


def real_smaps(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    vmas = parse_smaps(text)
    print(f"[输入] {path}:共 {len(vmas)} 个 VMA")
    s = summarize("真实进程", vmas)
    print("\n  对照:/proc/<pid>/status 的 VmRSS 应约等于上面的 RSS 合计,")
    print("        但 SMP 下 RSS 是异步统计的,可能有小偏差(要精确就扫 smaps)。")
    print(f"  VmSwap 只含匿名私有的 swap,不含 shmem —— 别用它当\"进程 swap 总量\"。")
    return s


def main():
    demo_pss_math()
    if len(sys.argv) > 1:
        real_smaps(sys.argv[1])
    else:
        print("\n[输入] 内置样例:两个进程各自有私有堆,并共享同一个 4 MB 共享库")
        print("       (内核真实 smaps 格式,可由命令行参数传入实际文件)")
        demo_shared_lib()
        demo_statm_notes()


def demo_statm_notes():
    print("\n=== /proc/[pid]/statm 的 7 个字段(注意哪些已经不能用)===")
    fields = [("size", "程序总大小(= VmSize)"),
              ("resident", "常驻部分(= VmRSS,页数)"),
              ("shared", "文件后备的页数(= RssFile + RssShmem)"),
              ("trs", "「代码页」——已损坏,含义不符"),
              ("lrs", "「库页」——2.6 起恒为 0"),
              ("drs", "「数据/栈页」——已损坏"),
              ("dt", "「脏页」——2.6 起恒为 0")]
    for n, d in fields:
        flag = "  <- 不要用" if n in ("trs", "lrs", "drs", "dt") else ""
        print(f"  {n:<10}{d}{flag}")
    print("\n  精确口径请用 /proc/<pid>/status 的 Vm* 或 smaps / smaps_rollup。")


if __name__ == "__main__":
    main()
