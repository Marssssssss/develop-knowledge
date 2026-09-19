"""差分火焰图（red/blue differential flame graph）—— 模型层。

权威来源：Brendan Gregg, "Differential Flame Graphs", 2014-11-09
https://www.brendangregg.com/blog/2014-11-09/differential-flame-graphs.html

原文确立的事实（README §2 逐条对应）：
1. 算法四步：取 profile 1 → 取 profile 2 → **用 2 画图**（所以宽度 = 第二份
   profile 的样本数）→ **用 "2 − 1" 的 delta 上色**：2 里更多为红，更少为蓝，
   饱和度相对 delta。
2. "The colors show the difference that function **directly** contributed
   (eg, being on-CPU), **not its children**." —— 折叠格式里每个栈一行，
   帧 a 的自有贡献只看 `a` 这一行，不含 `a;b`、`a;c`。
3. `difffolded.pl` 输出三列：`stack v1 v2`；`flamegraph.pl` 拿到三列输入
   就自动画成红/蓝差分图。
4. `-n`：**归一化第一份 profile 到第二份的量级**。不归一化的话，不同时段采集
   会因负载差异整体偏红/偏蓝。
5. `-x`：剥掉十六进制地址。符号化失败时原始地址会出现在栈里，两次采集地址不同
   会把同一函数误判成差异。
6. `--negate`：反转红蓝。因为**在 profile 2 里彻底消失的代码路径没有东西可以
   涂蓝**——反过来画（宽度用 1、颜色用 1−2 并取反）才能看到"即将发生什么"。
7. 消失路径的处理之二：**elided flame graph**，页面上只显示 "X% elided"，
   点进去才展开。
8. 另两种差分形态：Robert Mustacchi 的"只画差值"（宽度就是 delta，缺点是丢失
   全局上下文）；Cor-Paul Bezemer 的 flamegraphdiff（三视图，代价是矩形数翻倍）。
9. 同一套代码还能画 **CPI 火焰图**：差值不是两份 profile，而是 CPU cycles 与
   stall cycles。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

# 栈里的原始十六进制地址，形如 func+0x1a2b 或 0xffffffff81002000
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")

Profile = Dict[str, float]


# ------------------------------------------------------------ difffolded.pl ---


def diff_folded(p1: Profile, p2: Profile) -> Dict[str, Tuple[float, float]]:
    """合并两份折叠 profile 为 `stack -> (v1, v2)`；只在一侧出现的计 0。"""
    out: Dict[str, Tuple[float, float]] = {}
    for k in set(p1) | set(p2):
        out[k] = (p1.get(k, 0.0), p2.get(k, 0.0))
    return out


def format_three_column(d: Dict[str, Tuple[float, float]]) -> List[str]:
    """`difffolded.pl` 的输出：每行 `stack v1 v2`。"""
    return [f"{k} {v1:g} {v2:g}" for k, (v1, v2) in sorted(d.items())]


def parse_three_column(lines: Sequence[str]) -> Dict[str, Tuple[float, float]]:
    out: Dict[str, Tuple[float, float]] = {}
    for ln in lines:
        parts = ln.rstrip().rsplit(" ", 2)
        if len(parts) != 3:
            continue
        out[parts[0]] = (float(parts[1]), float(parts[2]))
    return out


def normalize_scale(p1: Profile, p2: Profile) -> float:
    """`-n` 的缩放因子：把第一份的样本总量拉平到第二份。"""
    s1 = sum(p1.values())
    if s1 <= 0:
        return 1.0
    return sum(p2.values()) / s1


def normalize(p1: Profile, p2: Profile) -> Profile:
    """`-n`：归一化后的第一份 profile。"""
    k = normalize_scale(p1, p2)
    return {s: v * k for s, v in p1.items()}


def strip_hex(stack: str) -> str:
    """`-x`：剥掉十六进制地址，避免同一函数因地址不同被当成差异。"""
    return _HEX_RE.sub("", stack)


def apply_strip_hex(p: Profile) -> Profile:
    out: Profile = {}
    for s, v in p.items():
        out[strip_hex(s)] = out.get(strip_hex(s), 0.0) + v
    return out


# ---------------------------------------------------------------- 差分着色 ---


@dataclass(frozen=True)
class DiffFrame:
    """差分火焰图的一帧。"""

    stack: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        """颜色取 "2 − 1"。"""
        return self.after - self.before

    @property
    def width(self) -> float:
        """形状（宽度）取第二份 profile。"""
        return self.after

    def hue(self, max_delta: float) -> str:
        """红=增长，蓝=减少，饱和度随 |delta|/max|delta|；差为 0 是白色。"""
        if max_delta <= 0 or abs(self.delta) < 1e-12:
            return "white"
        r = self.delta / max_delta
        sat = min(abs(r), 1.0)
        return f"red@{sat:.3f}" if r > 0 else f"blue@{sat:.3f}"


def build_frames(d: Dict[str, Tuple[float, float]], negate: bool = False) -> List[DiffFrame]:
    """把三列数据变成待绘制的帧列表；negate=True 对应 `--negate`。"""
    frames = []
    for stack, (v1, v2) in d.items():
        before, after = (v2, v1) if negate else (v1, v2)
        frames.append(DiffFrame(stack, before, after))
    return frames


def max_abs_delta(frames: Sequence[DiffFrame]) -> float:
    return max((abs(f.delta) for f in frames), default=0.0)


def total_width(frames: Sequence[DiffFrame]) -> float:
    """图的总宽度 = 第二份 profile 的样本总量（negate 时是第一份）。"""
    return sum(f.width for f in frames)


# ----------------------------------------------------------------- 消失路径 ---


def elided(p1: Profile, p2: Profile) -> Dict[str, float]:
    """在 profile 1 里存在、profile 2 里彻底消失的栈，及其占 profile 1 的比例。

    这就是页面上 "X% elided" 的来源：它们**没有东西可以涂蓝**。
    """
    total1 = sum(p1.values())
    gone = {s: v for s, v in p1.items() if s not in p2}
    return {
        "count": float(len(gone)),
        "samples": sum(gone.values()),
        "pct": (sum(gone.values()) * 100.0 / total1) if total1 > 0 else 0.0,
    }


def self_delta(profile_stack: str, d: Dict[str, Tuple[float, float]]) -> float:
    """帧**自身**的 delta：只看该栈这一行，不含其子栈。"""
    if profile_stack not in d:
        return 0.0
    v1, v2 = d[profile_stack]
    return v2 - v1


def subtree_delta(prefix: str, d: Dict[str, Tuple[float, float]]) -> float:
    """`prefix` 及其全部子孙的 delta 之和（用于说明"颜色不含孩子"）。"""
    return sum((v2 - v1) for s, (v1, v2) in d.items() if s == prefix or s.startswith(prefix + ";"))


# -------------------------------------------------------------- 其它差分形态 --


def mustacchi_width(f: DiffFrame) -> float:
    """Robert Mustacchi 的方案：宽度就是 delta，只画差值。"""
    return f.delta


def cpi_frame(stack: str, cycles: float, stall_cycles: float) -> DiffFrame:
    """CPI 火焰图：差值不是两份 profile，而是 cycles 与 stall cycles。"""
    return DiffFrame(stack, stall_cycles, cycles)
