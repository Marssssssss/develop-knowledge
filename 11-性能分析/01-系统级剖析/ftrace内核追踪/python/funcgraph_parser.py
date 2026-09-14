#!/usr/bin/env python3
"""funcgraph_parser.py — 解析 ftrace function_graph 输出,还原调用树并算 self/inclusive 耗时

为什么要自己解析:function_graph 吐的是"每个函数进出各一行"的文本,
要拿到"哪个函数自己花的时间最多"(火焰图里平台宽度),必须:
  1. 用 { / } 配对还原调用树(不要依赖缩进宽度——它随内核版本变化)
  2. inclusive(出口行上的耗时) 减去所有子节点 inclusive = self time
  3. 叶子函数(单行 `func();`)的 inclusive == self

权威依据:docs.kernel.org/trace/ftrace.html 的 function_graph 输出格式与
overhead 标记阈值($ > 1s, @ > 100ms, * > 10ms, # > 1000us, ! > 100us, + > 10us)。

用法:
    python3 funcgraph_parser.py            # 解析内置样例
    python3 funcgraph_parser.py trace.txt  # 解析真实 trace 文件
"""

import re
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------- 内置样例
SAMPLE = """# tracer: function_graph
#
# CPU  DURATION                  FUNCTION CALLS
# |     |   |                     |   |   |   |

 0)               |  sys_open() {
 0)               |    do_sys_open() {
 0)               |      getname() {
 0)               |        kmem_cache_alloc() {
 0)   1.382 us    |          __might_sleep();
 0)   2.478 us    |        }
 0)   9.500 us    |      }
 0)  ! 120.500 us  |    }
 0)   * 11.000 ms  |  }
 0)               |  sys_read() {
 0)   0.812 us    |    ktime_get_ts64();
 0)   2.100 us    |  }
 1)               |  do_softirq() {
 1)   # 1500.000 us |    net_rx_action();
 1)   # 1800.000 us |  }
"""

UNIT_US = {"us": 1.0, "ms": 1000.0, "s": 1_000_000.0}
# 头部: cpu)   [duration 列] |  [函数体]
LINE = re.compile(r"^\s*(?P<cpu>\d+)\)\s*(?P<col>[^|]*)\|\s*(?P<body>.*?)\s*$")
# duration 列里可能是 "12.000 us" 或 "! 120.500 us" 或 "* 11.000 ms"
DUR = re.compile(r"(?P<marker>[$@*#!+])?\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>us|ms|s)\b")


@dataclass
class Node:
    """调用树的一个节点:inclusive 来自出口行,self = inclusive - Σ children"""
    name: str
    depth: int
    cpu: int
    inclusive: float = 0.0          # 微秒
    marker: str = " "
    closed: bool = False
    children: list = field(default_factory=list)

    @property
    def self_time(self) -> float:
        return self.inclusive - sum(c.inclusive for c in self.children)


def parse_duration(col: str):
    """从 duration 列取出 (微秒, overhead 标记)。没有耗时则返回 (None, ' ')"""
    m = DUR.search(col)
    if not m:
        return None, " "
    return float(m.group("val")) * UNIT_US[m.group("unit")], (m.group("marker") or " ")


def parse(text: str):
    """返回 (roots, stats)。用 { / } 配对还原调用树。"""
    roots, stack, leaves, unclosed = [], [], 0, 0
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE.match(raw)
        if not m:
            continue
        body = re.sub(r"/\*.*?\*/", "", m.group("body")).strip()   # 去掉 /* 注释 */
        if not body:
            continue
        dur, marker = parse_duration(m.group("col"))
        cpu = int(m.group("cpu"))

        if body.endswith("{"):                                     # 入口行
            stack.append(Node(body[:-1].strip(), len(stack), cpu, marker=marker))
        elif body.startswith("}"):                                 # 出口行(带 inclusive)
            if not stack:
                continue
            node = stack.pop()
            node.inclusive = dur if dur is not None else 0.0
            node.marker = marker
            node.closed = True
            (stack[-1].children if stack else roots).append(node)
        elif body.endswith(";"):                                   # 叶子:inclusive == self
            name = body[:-1].strip()
            node = Node(name, len(stack), cpu, dur or 0.0, marker, True)
            leaves += 1
            (stack[-1].children if stack else roots).append(node)
    unclosed = len(stack)
    return roots, {"leaves": leaves, "unclosed": unclosed}


# --------------------------------------------------------------- 报表
def walk(nodes, depth=0):
    for n in nodes:
        yield depth, n
        yield from walk(n.children, depth + 1)


def print_tree(roots):
    print("=== 调用树(inclusive / self,单位 us)===")
    for depth, n in walk(roots):
        flag = "" if n.closed else "  <-- 未闭合(输出被截断)"
        print(f"{'  ' * depth}{n.name:<24} incl={n.inclusive:>10.3f} "
              f"self={n.self_time:>10.3f} cpu={n.cpu} marker='{n.marker}'{flag}")


def print_top_self(roots, top=6):
    print(f"\n=== Top {top} 按 self time 排序(真正的热点)===")
    flat = [(n.self_time, d, n) for d, n in walk(roots)]
    flat.sort(reverse=True)
    for acc, depth, n in flat[:top]:
        share = 100.0 * acc / sum(x[0] for x in flat) if flat else 0.0
        print(f"  {n.name:<24} self={acc:>10.3f} us  ({share:5.1f}%)  调用深度={depth}")


def print_folded(roots):
    """输出 folded stacks(on-CPU 火焰图的输入格式):栈用 ; 连接,计数取该帧的 self time"""
    print("\n=== folded stacks(可直接喂 flamegraph.pl)===")

    def emit(nodes, prefix):
        for n in nodes:
            stack = prefix + [n.name]
            # 每个节点自成一帧:计数 = 它"作为栈顶"独占的时间(self time)
            print(f"{';'.join(stack)} {int(round(n.self_time * 1000))}")
            emit(n.children, stack)

    emit(roots, [])
    total = sum(n.self_time for _, n in walk(roots))
    print(f"total self = {total:.3f} us(应等于 Σroot inclusive)")


def print_markers(roots):
    names = {"$": "> 1 s", "@": "> 100 ms", "*": "> 10 ms", "#": "> 1000 us",
             "!": "> 100 us", "+": "> 10 us"}
    hits = [(n.marker, n.name, n.inclusive) for _, n in walk(roots) if n.marker in names]
    print("\n=== overhead 标记(内核自己标出的高延迟函数)===")
    if not hits:
        print("  (无)")
    for marker, name, dur in sorted(hits, key=lambda x: -x[2]):
        print(f"  '{marker}' {name:<24} {dur:>10.3f} us  阈值 {names[marker]}")


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        print(f"[输入] {sys.argv[1]}")
    else:
        text = SAMPLE
        print("[输入] 内置样例(摘自 kernel.org function_graph 输出格式)")

    roots, stats = parse(text)
    print(f"[解析] 根节点 {len(roots)} 个,叶子行 {stats['leaves']} 条,"
          f"未闭合 {stats['unclosed']} 个\n")

    print_tree(roots)
    print_top_self(roots)
    print_folded(roots)
    print_markers(roots)

    # 自检:所有节点 self 之和 == 所有根节点 inclusive 之和(树的一致性不变量)
    flat_self = sum(n.self_time for _, n in walk(roots))
    root_incl = sum(n.inclusive for n in roots)
    ok = abs(flat_self - root_incl) < 1e-6
    print(f"\n[不变量] Σself={flat_self:.3f} us, Σroot_inclusive={root_incl:.3f} us, "
          f"一致 = {ok}")


if __name__ == "__main__":
    main()
