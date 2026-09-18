#!/usr/bin/env python3
"""污点分析的精确性阶梯：flow-insensitive / flow-sensitive / path-sensitive。

对应 CodeQL 的两套库（见 codeql.github.com 的 Analyzing data flow in Python）：
  * ``DataFlow``     —— 只跟踪**保值**（value-preserving）步骤；
  * ``TaintTracking`` —— 在 DataFlow 之上追加**非保值**步骤（如 ``y = "SELECT " + x``）。
配置面是 ``ConfigSig`` 的 ``isSource`` / ``isSink`` / ``isBarrier``（barrier 即 sanitizer）。

本文件用一条极小 IR 复现三档精度的判定差异，把「误报来自哪里、漏报来自哪里」量化出来。

IR 语法（语句为元组）：
    ("let", dst, expr)                 赋值
    ("sink", label, var)               危险调用（sink）
    ("setflag", name, bool)            设置一个布尔标记（值已知，用于路径可行性）
    ("if", flag, then_body, else_body) 分支；else_body 可为 []

expr:
    ("source",)          source
    ("const",)           常量（干净，非保值意义上的"新值"）
    ("copy", v)          直接拷值 —— 保值步骤
    ("cat", a, b)        字符串拼接 —— **非保值**步骤
    ("sanitize", v)      sanitizer（isBarrier）
    ("fromflag", name)   把布尔标记的值读进变量 —— **隐式流**（implicit flow）
"""

from typing import Dict, List, Optional, Tuple

Stmt = tuple
Env = Dict[str, bool]
# flag_env 里 None 表示"该标记在这条路径上取值不确定"（分支合并后冲突）
FlagEnv = Dict[str, Optional[bool]]


# ---------------------------------------------------------------- 表达式求值

def eval_expr(e: tuple, env: Env, flags: FlagEnv, mode: str) -> bool:
    """返回表达式结果是否被污染。mode ∈ {"flow", "taint"}。"""
    kind = e[0]
    if kind == "source":
        return True
    if kind == "const":
        return False
    if kind == "copy":
        return bool(env.get(e[1], False))
    if kind == "cat":
        # 非保值步骤：DataFlow 看不到，TaintTracking 才传播
        if mode == "flow":
            return False
        return eval_expr(e[1], env, flags, mode) or eval_expr(e[2], env, flags, mode)
    if kind == "sanitize":
        return False  # isBarrier：无论入参如何，出参干净
    if kind == "fromflag":
        # 隐式流（数据通过控制流而非赋值传递）：两种模式都不跟踪 → 漏报
        return False
    raise ValueError("unknown expr: %r" % (kind,))


def _walk(stmts: List[Stmt], out: List[Stmt]) -> None:
    """把嵌套分支展平（flow-insensitive 分析忽略程序顺序与分支结构）。"""
    for s in stmts:
        if s[0] == "if":
            _walk(s[2], out)
            _walk(s[3], out)
        else:
            out.append(s)


# ------------------------------------------------- 1) flow-insensitive（最粗）

def flow_insensitive(prog: List[Stmt], mode: str = "taint") -> List[str]:
    """忽略语句顺序与分支：把所有赋值看成一组约束，迭代到不动点。

    这是最便宜的全局污点分析，也是误报的主要来源 —— 它分不清
    「先读后写」与「先写后读」。
    """
    flat: List[Stmt] = []
    _walk(prog, flat)

    env: Env = {}
    changed = True
    while changed:  # 不动点迭代
        changed = False
        for s in flat:
            if s[0] != "let":
                continue
            dst, e = s[1], s[2]
            # 关键：flow-insensitive 是**所有赋值的并集**（may-信息），不是"后写覆盖"。
            # 用覆盖语义会在「同一变量有两个冲突赋值」时振荡不收敛（P4 就死在这里）；
            # 取 OR 后格点只升不降，必然收敛。
            val = eval_expr(e, env, {}, mode) or env.get(dst, False)
            if env.get(dst, False) != val:
                env[dst] = val
                changed = True
    # 报告：任何 sink 的参数在 env 中被污染
    return [s[1] for s in flat if s[0] == "sink" and env.get(s[2], False)]


# --------------------------------------- 2) flow-sensitive / path-insensitive

def _merge(a: Env, b: Env) -> Env:
    keys = set(a) | set(b)
    return {k: bool(a.get(k, False)) or bool(b.get(k, False)) for k in keys}


def _merge_flags(a: FlagEnv, b: FlagEnv) -> FlagEnv:
    keys = set(a) | set(b)
    out: FlagEnv = {}
    for k in keys:
        va, vb = a.get(k), b.get(k)
        out[k] = va if va == vb else None  # 两侧不一致 → 不确定
    return out


def flow_sensitive(prog: List[Stmt], mode: str = "taint") -> List[str]:
    """按程序顺序走，分支在汇合点做**并集**（丢失分支间的相关性）。"""
    reports: List[str] = []

    def run(stmts: List[Stmt], env: Env, flags: FlagEnv) -> Tuple[Env, FlagEnv]:
        for s in stmts:
            if s[0] == "let":
                env[s[1]] = eval_expr(s[2], env, flags, mode)
            elif s[0] == "sink":
                if env.get(s[2], False):
                    reports.append(s[1])
            elif s[0] == "setflag":
                flags[s[1]] = s[2]
            elif s[0] == "if":
                then_env, then_fl = run(s[2], dict(env), dict(flags))
                else_env, else_fl = run(s[3], dict(env), dict(flags))
                env.clear(); env.update(_merge(then_env, else_env))
                flags.clear(); flags.update(_merge_flags(then_fl, else_fl))
        return env, flags

    run(prog, {}, {})
    return reports


# --------------------------------------------------------- 3) path-sensitive

def path_sensitive(prog: List[Stmt], mode: str = "taint") -> List[str]:
    """枚举可行路径。分支条件已知时只走可行的一侧 —— 保住分支间的相关性。"""
    reports = set()

    def run(stmts, env, flags):
        for i, s in enumerate(stmts):
            if s[0] == "let":
                env[s[1]] = eval_expr(s[2], env, flags, mode)
            elif s[0] == "sink":
                if env.get(s[2], False):
                    reports.add(s[1])
            elif s[0] == "setflag":
                flags[s[1]] = s[2]
            elif s[0] == "if":
                val = flags.get(s[1])
                if val is None:
                    branches = [s[2], s[3]]  # 取值未知 → 两侧都可能
                elif val:
                    branches = [s[2]]
                else:
                    branches = [s[3]]
                # 分叉后**必须带上 if 之后的语句**继续执行，否则 if 后面的 sink
                # 永远走不到（P4 的 sink 就挂在 if 之后）。
                rest = stmts[i + 1:]
                for b in branches:
                    run(list(b) + rest, dict(env), dict(flags))
                return

    run(prog, {}, {})
    return sorted(reports)


# ------------------------------------------------------------------- 测试程序

def programs() -> Dict[str, List[Stmt]]:
    return {
        # P1 先读后写：flow-insensitive 分不清顺序，产生误报
        "P1_read_before_write": [
            ("sink", "S", "y"),
            ("let", "y", ("source",)),
        ],
        # P2 非保值步骤：DataFlow 漏报、TaintTracking 命中
        "P2_concat": [
            ("let", "x", ("source",)),
            ("let", "q", ("cat", ("const",), ("copy", "x"))),
            ("sink", "S", "q"),
        ],
        # P3 sanitizer 作为 barrier：三档都应判干净
        "P3_sanitizer": [
            ("let", "x", ("source",)),
            ("let", "y", ("sanitize", ("copy", "x"))),
            ("sink", "S", "y"),
        ],
        # P4 相关分支：只有 x 被 sanitize 时 ok 才为 True，
        #    path-insensitive 合并后丢失相关性 → 误报
        "P4_correlated_flag": [
            ("let", "x", ("source",)),
            ("setflag", "ok", False),
            ("if", "cond", [
                ("let", "x", ("sanitize", ("copy", "x"))),
                ("setflag", "ok", True),
            ], []),
            ("if", "ok", [("sink", "S", "x")], []),
        ],
        # P4' 打掉相关性：两条分支都置 ok=True，此时确实存在漏洞路径
        "P4b_broken_correlation": [
            ("let", "x", ("source",)),
            ("setflag", "ok", False),
            ("if", "cond", [
                ("let", "x", ("sanitize", ("copy", "x"))),
                ("setflag", "ok", True),
            ], [("setflag", "ok", True)]),
            ("if", "ok", [("sink", "S", "x")], []),
        ],
        # P5 隐式流：数据经控制流传递，三档全部漏报
        "P5_implicit_flow": [
            ("let", "x", ("source",)),
            ("if", "x", [("setflag", "admin", True)], [("setflag", "admin", False)]),
            ("let", "y", ("fromflag", "admin")),
            ("sink", "S", "y"),
        ],
        # P6 真阳性：任何精度都该报出来
        "P6_true_positive": [
            ("let", "x", ("source",)),
            ("let", "y", ("copy", "x")),
            ("sink", "S", "y"),
        ],
    }


def run_all() -> Dict[str, Dict[str, List[str]]]:
    out = {}
    for name, prog in programs().items():
        out[name] = {
            "insensitive/taint": flow_insensitive(prog, "taint"),
            "insensitive/flow": flow_insensitive(prog, "flow"),
            "sensitive/taint": flow_sensitive(prog, "taint"),
            "sensitive/flow": flow_sensitive(prog, "flow"),
            "path/taint": path_sensitive(prog, "taint"),
        }
    return out


TRUTH = {
    "P1_read_before_write": [],      # 读到的是写入前的值 → 干净
    "P2_concat": ["S"],              # 拼接确实把污点带进 sink
    "P3_sanitizer": [],              # barrier 生效
    "P4_correlated_flag": [],        # 可行路径上 x 已被净化
    "P4b_broken_correlation": ["S"],  # 存在未净化的可行路径
    "P5_implicit_flow": ["S"],       # 现实中 y 完全由 x 决定 → 应报但报不出
    "P6_true_positive": ["S"],
}


if __name__ == "__main__":
    res = run_all()
    hdr = "%-26s %-20s %-20s %-20s %s" % (
        "program", "insensitive", "sensitive", "path", "truth")
    print(hdr)
    print("-" * len(hdr))
    for name, r in res.items():
        print("%-26s %-20s %-20s %-20s %s" % (
            name,
            ",".join(r["insensitive/taint"]) or "-",
            ",".join(r["sensitive/taint"]) or "-",
            ",".join(r["path/taint"]) or "-",
            ",".join(TRUTH[name]) or "-",
        ))
    print()
    print("flow-mode（只跟保值步骤）: P2 ->",
          ",".join(res["P2_concat"]["sensitive/flow"]) or "-（漏报）")
