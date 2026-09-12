#!/usr/bin/env python3
"""火焰图生成 demo:folded 栈 -> 交互式 SVG(复现 flamegraph.pl 核心渲染)。

实现官方工具的关键语义(对照源码逐条):
  1. 解析  "frame;frame;... 计数"(惰性匹配行首栈 + 行尾数字);
  2. 排序  sort @Data:按帧名字母序最大化合并(x 轴无时间含义);
  3. 剪枝  宽度 < minwidth(默认 0.1px)的帧丢弃;
  4. 宽度  widthpertime = (imagewidth - 2*xpad) / timemax;
  5. 颜色  暖色 r=205+50v3 / g=230v1 / b=55v2,名字校验和作种子保证同名同色;
  6. 转义  & < > " ,且 & 最先替换;
  7. 交互  悬停详情 + 点击缩放(内嵌 JS 的最小子集)。

用法:
    python3 main.py            # 用内置示例样本生成 flamegraph.svg
    cat out.folded | python3 main.py -          # 读标准输入
    python3 main.py out.folded --title="CPU"
"""
from __future__ import annotations

import argparse
import random
import sys

# ---------------- 图形参数(默认值取自 flamegraph.pl) ----------------
imagewidth = 1200.0
frameheight = 16.0
fontsize = 12.0
fontwidth = 0.59 * fontsize          # 官方:0.59 * fontsize 估字符宽
minwidth = 0.1
xpad = 10.0
ypad1 = 10.0
ypad2 = 10.0
xpad2 = 10.0

# 内置示例:模拟一个多线程应用的 60 样本剖析(folded 格式)
BUILTIN_FOLDED = """\
main;net_server;accept_loop;epoll_wait 8
main;net_server;accept_loop;accept;connection_new 6
main;net_server;worker_pool;worker_run;parse_request;json_parse 14
main;net_server;worker_pool;worker_run;parse_request;validate 5
main;net_server;worker_pool;worker_run;handle_query;db_exec;btree_search 18
main;net_server;worker_pool;worker_run;handle_query;db_exec;row_serialize 6
main;net_server;logger;syslog_write;write_syscall 3
"""


# ---------------- 1. 解析 folded ----------------
def parse_folded(text: str) -> dict[str, float]:
    """解析每行 "stack;frames count",失败行跳过(对齐官方 Ignored N 行为)。"""
    counts: dict[str, float] = {}
    ignored = 0
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        # 惰性匹配栈 + 行尾整数/小数计数(与 flamegraph.pl 正则等价)
        stack, sep, cnt = line.rpartition(" ")
        if not sep or not stack or not _is_number(cnt):
            ignored += 1
            continue
        counts[stack] = counts.get(stack, 0.0) + float(cnt)
    if ignored:
        print(f"warning: Ignored {ignored} lines with invalid format", file=sys.stderr)
    return counts


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---------------- 5. 颜色(暖色 + 名字种子伪随机) ----------------
def name_hash(name: str) -> int:
    """名字校验和作种子:同名函数跨图颜色一致(官方 sum_namehash 语义)。"""
    return sum(b & 0xFF for b in name.encode("utf-8", "replace"))


def warm_color(name: str) -> str:
    rng = random.Random(name_hash(name))       # srand($hash)
    v1, v2, v3 = rng.random(), rng.random(), rng.random()
    r = 205 + int(50 * v3)                     # 205~255
    g = int(230 * v1)                           # 0~230
    b = int(55 * v2)                            # 0~55
    return f"rgb({r},{g},{b})"


# ---------------- 6. SVG 转义 ----------------
def esc(s: str) -> str:
    s = s.replace("&", "&amp;")                # & 必须最先
    s = s.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return s


# ---------------- 前缀树 ----------------
class Node:
    __slots__ = ("name", "value", "children")

    def __init__(self, name: str) -> None:
        self.name = name
        self.value = 0.0
        self.children: dict[str, "Node"] = {}

    def add(self, frames: list[str], count: float) -> None:
        self.value += count
        if not frames:
            return
        child = self.children.setdefault(frames[0], Node(frames[0]))
        child.add(frames[1:], count)


def build_tree(counts: dict[str, float]) -> Node:
    root = Node("")                             # 根帧占满 timemax
    for stack in sorted(counts):                # sort @Data:字母序
        root.add(stack.split(";"), counts[stack])
    return root


# ---------------- 3/4/7. 渲染 SVG ----------------
def render(root: Node, title: str, countname: str = "samples") -> str:
    timemax = root.value
    depthmax = _depth(root) - 1
    widthpertime = (imagewidth - 2 * xpad) / (timemax or 1)
    height = (depthmax + 1) * frameheight + ypad1 + ypad2 + 30.0

    out: list[str] = []
    out.append(
        f'<?xml version="1.0" standalone="no"?>\n'
        f'<svg version="1.1" width="{int(imagewidth)}" height="{int(height)}" '
        f'onload="init()" xmlns="http://www.w3.org/2000/svg">\n'
        f'<style>text{{font-family:Verdana,sans-serif;font-size:{fontsize}px}}'
        f'.detail{{font-weight:bold}}</style>'
    )
    # 标题行(title / total / detail 三处悬停更新目标)
    out.append(
        f'<text x="{xpad}" y="{ypad1 - 3}" text-anchor="left" class="detail" '
        f'id="title">{esc(title)}</text>'
        f'<text x="{xpad}" y="{ypad1 + frameheight * (depthmax + 1) + ypad2 - 3}" '
        f'id="total">all ({int(timemax)} {countname}, 100%)</text>'
        f'<text x="{imagewidth - xpad2}" y="{ypad1 + frameheight * (depthmax + 1) + ypad2 - 3}" '
        f'text-anchor="end" id="detail"> </text>'
    )

    def emit(node: Node, depth: int, x_start: float) -> None:
        """按字母序自左向右铺帧;宽度 = 计数 * widthpertime。"""
        x = x_start
        for name in sorted(node.children):
            child = node.children[name]
            w = child.value * widthpertime
            if w < minwidth:                    # 剪枝(官方 minwidth 语义)
                continue
            y1 = ypad1 + depth * frameheight
            chars = int(w / fontwidth)
            label = name if chars >= 3 else ""  # 至少 3 字符才绘制
            if len(label) > chars and chars >= 3:
                label = label[: max(chars - 2, 1)] + ".."
            pct = 100.0 * child.value / timemax
            out.append(
                f'<g><title>{esc(name)} ({int(child.value)} {countname}, {pct:.2f}%)</title>'
                f'<rect x="{x:.2f}" y="{y1:.2f}" width="{w:.2f}" height="{frameheight - 1:.2f}" '
                f'fill="{warm_color(name)}" rx="1"/>'
                + (f'<text x="{x + 3:.2f}" y="{y1 + frameheight - 4:.2f}">{esc(label)}</text>' if label else "")
                + "</g>"
            )
            emit(child, depth + 1, x)           # 深度 +1 = 图更"平"即栈更深
            x += w

    # 根帧:强制占满整幅宽度(官方:$func eq "" and $depth == 0)
    out.append(
        f'<g><title>all ({int(timemax)} {countname}, 100%)</title>'
        f'<rect x="{xpad}" y="{ypad1}" width="{timemax * widthpertime:.2f}" '
        f'height="{frameheight - 1:.2f}" fill="rgb(255,255,255)"/></g>'
    )
    emit(root, 1, xpad)

    # 交互 JS 最小子集:悬停填 detail、点击缩放(官方 zoom 的简化)
    out.append(
        """<script><![CDATA[
var details, svg;
function init() {
    svg = document.querySelector('svg');
    details = document.getElementById('detail');
    svg.addEventListener('mouseover', function (ev) {
        var t = ev.target.closest('g');
        if (t) details.textContent = t.querySelector('title').textContent;
    });
    svg.addEventListener('click', function (ev) {
        var t = ev.target.closest('g');
        if (!t) return;
        var r = t.getBoundingClientRect(), s = svg.getBoundingClientRect();
        if (!svg.viewBox.baseVal.width)
            svg.setAttribute('viewBox', '0 0 ' + s.width + ' ' + s.height);
        var vb = svg.viewBox.baseVal;
        var zoom = vb.width / ((r.width / s.width) * vb.width) * 0.9; // 点击帧放大到 90% 画布宽
        var cx = vb.x + (r.left - s.left) / s.width * vb.width;
        svg.setAttribute('viewBox', (cx - (r.width / s.width) * vb.width * zoom / 2)
            + ' ' + vb.y + ' ' + (vb.width / zoom) + ' ' + vb.height);
    });
}
]]></script>"""
    )
    out.append("</svg>")
    return "\n".join(out)


def _depth(node: Node) -> int:
    return 1 + max((_depth(c) for c in node.children.values()), default=0)


def main() -> int:
    ap = argparse.ArgumentParser(description="flamegraph.pl 的 Python 复现(核心子集)")
    ap.add_argument("input", nargs="?", default="-", help="folded 文件路径,'-' 读 stdin")
    ap.add_argument("--title", default="Flame Graph")
    ap.add_argument("--countname", default="samples")
    ap.add_argument("-o", "--output", default="flamegraph.svg")
    args = ap.parse_args()

    text = BUILTIN_FOLDED if args.input == "-" else open(args.input, encoding="utf-8").read()
    counts = parse_folded(text)
    if not counts:
        print("ERROR: No valid input provided", file=sys.stderr)
        return 2
    if sum(counts.values()) < 100:
        print("warning: <100 samples; results may be unreliable", file=sys.stderr)

    svg = render(build_tree(counts), args.title, args.countname)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"[flamegraph-py] wrote {args.output} ({sum(counts.values())} {args.countname})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
