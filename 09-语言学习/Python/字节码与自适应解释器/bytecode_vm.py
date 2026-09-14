#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CPython 字节码与自适应解释器(PEP 659)教学 demo。

五个部分互相独立:
  §1 教学用极简指令集 + 栈式 VM           -- 亲手执行一遍"栈机器"
  §2 真实 code object 剖析(dis)          -- co_code/co_consts/co_names/co_stacksize
  §3 EXTENDED_ARG 与 CACHE 内联缓存       -- 1 字节操作数上限如何被突破
  §4 自适应专用化(quickening)模拟         -- PEP 659 的预热 + 饱和计数器 + 去优化
  §5 静态栈深度 vs co_stacksize           -- 编译器如何求 co_stacksize

注意:字节码是 CPython 实现细节。dis 文档原文:
"Bytecode is an implementation detail of the CPython interpreter. No guarantees are
made that bytecode will not be added, removed, or changed between versions of Python."
输出随版本变化,代码只断言跨版本稳定的部分。

运行:python bytecode_vm.py
"""

import dis
import sys

# ---------------------------------------------------------------------------
# §1 教学用极简指令集:数值与 CPython 无关,只对齐"栈机器"语义。
#    dis 文档用 STACK 描述解释器栈:"The top of the stack corresponds to STACK[-1]".
# ---------------------------------------------------------------------------
(LOAD_CONST, LOAD_FAST, STORE_FAST, LOAD_NAME, STORE_NAME, BINARY_OP,
 COMPARE_OP, POP_TOP, JUMP_IF_FALSE, JUMP_ABSOLUTE, RETURN_VALUE) = range(11)

OPNAMES = {LOAD_CONST: "LOAD_CONST", LOAD_FAST: "LOAD_FAST", STORE_FAST: "STORE_FAST",
           LOAD_NAME: "LOAD_NAME", STORE_NAME: "STORE_NAME", BINARY_OP: "BINARY_OP",
           COMPARE_OP: "COMPARE_OP", POP_TOP: "POP_TOP",
           JUMP_IF_FALSE: "JUMP_IF_FALSE", JUMP_ABSOLUTE: "JUMP_ABSOLUTE",
           RETURN_VALUE: "RETURN_VALUE"}
BINOPS = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b}


class MiniCode:
    """对应 CPython 的 code object:指令序列 + 常量表 + 名字表 + 局部变量表。"""

    def __init__(self, consts, names, instructions, argnames=()):
        self.co_consts = consts
        self.co_names = names
        self.co_varnames = list(argnames)
        self.instructions = instructions          # [(opcode, arg), ...]

    def dis(self):
        """打印反汇编,格式对齐 dis 的 "offset opname arg argrepr" 四列。"""
        for offset, (op, arg) in enumerate(self.instructions):
            if op == LOAD_CONST:
                extra = repr(self.co_consts[arg])
            elif op in (LOAD_FAST, STORE_FAST):
                extra = self.co_varnames[arg]
            elif op in (LOAD_NAME, STORE_NAME):
                extra = self.co_names[arg]
            elif op == BINARY_OP:
                extra = list(BINOPS)[arg]
            else:
                extra = ""
            print(f"  {offset:>3} {OPNAMES[op]:<14} {arg:<4} {extra}")


def run_mini(code, args):
    """栈式求值循环。CPython 原文:LOAD/STORE 系列操作 STACK[-1];
    BINARY_OP 等价于 "rhs = STACK.pop(); lhs = STACK.pop(); STACK.append(lhs op rhs)"。
    返回 (返回值, trace);trace 每项为 (pc, opname, 执行后的栈快照)。
    """
    stack, local, global_, pc, trace = [], dict(args), {}, 0, []
    while pc < len(code.instructions):
        op, arg = code.instructions[pc]
        if op == LOAD_CONST:
            stack.append(code.co_consts[arg])
        elif op == LOAD_FAST:
            stack.append(local[code.co_varnames[arg]])
        elif op == STORE_FAST:
            local[code.co_varnames[arg]] = stack.pop()
        elif op == LOAD_NAME:
            name = code.co_names[arg]
            stack.append(global_[name] if name in global_ else local[name])
        elif op == STORE_NAME:
            global_[code.co_names[arg]] = stack.pop()
        elif op == BINARY_OP:
            rhs, lhs = stack.pop(), stack.pop()
            stack.append(BINOPS[list(BINOPS)[arg]](lhs, rhs))
        elif op == COMPARE_OP:
            rhs, lhs = stack.pop(), stack.pop()
            stack.append(lhs < rhs)
        elif op == POP_TOP:
            stack.pop()
        elif op == JUMP_IF_FALSE:                 # 弹栈;为假则跳转
            if not stack.pop():
                pc = arg
                continue
        elif op == JUMP_ABSOLUTE:                 # 无条件跳转,不动栈
            pc = arg
            continue
        elif op == RETURN_VALUE:                  # "Returns with STACK[-1] to the caller"
            trace.append((pc, OPNAMES[op], tuple(stack)))
            return stack[-1], trace
        trace.append((pc, OPNAMES[op], tuple(stack)))
        pc += 1
    return None, trace


def demo_mini_vm():
    print("=" * 74)
    print("[1] 栈机器教学 VM:`def f(a, b): return (a + b) * (a - b)`")
    print("=" * 74)
    code = MiniCode(consts=[], names=[], argnames=["a", "b"],
                    instructions=[(LOAD_FAST, 0), (LOAD_FAST, 1), (BINARY_OP, 0),
                                  (LOAD_FAST, 0), (LOAD_FAST, 1), (BINARY_OP, 1),
                                  (BINARY_OP, 2), (RETURN_VALUE, 0)])
    code.dis()
    result, trace = run_mini(code, {"a": 7, "b": 3})
    print("-- 逐步执行 f(7, 3),行末为执行后的栈 --")
    for pc, name, stack in trace:
        print(f"  pc={pc} {name:<14} stack={list(stack)}")
    print(f"  结果 = {result}(期望 (7+3)*(7-3) = 40)\n")

    print("-- 循环版:sum += i for i in 1..5;JUMP_IF_FALSE/JUMP_ABSOLUTE 展示回跳 --")
    loop = MiniCode(consts=[0, 1, 6], names=["sum", "i"],
                    instructions=[(LOAD_CONST, 0), (STORE_NAME, 0),        # sum = 0
                                  (LOAD_CONST, 1), (STORE_NAME, 1),        # i = 1
                                  (LOAD_NAME, 1), (LOAD_CONST, 2), (COMPARE_OP, 0),
                                  (JUMP_IF_FALSE, 17),                     # i < 6 ?
                                  (LOAD_NAME, 0), (LOAD_NAME, 1), (BINARY_OP, 0),
                                  (STORE_NAME, 0),                         # sum += i
                                  (LOAD_NAME, 1), (LOAD_CONST, 1), (BINARY_OP, 0),
                                  (STORE_NAME, 1),                         # i += 1
                                  (JUMP_ABSOLUTE, 4),                      # 回到判断
                                  (LOAD_NAME, 0), (RETURN_VALUE, 0)])
    result, trace = run_mini(loop, {})
    print(f"  结果 sum = {result}(期望 15);共执行 {len(trace)} 条指令"
          f"(真实 CPython 自 3.10 起跳转参数也是指令偏移而非字节偏移)\n")


# ---------------------------------------------------------------------------
# §2 真实 code object 剖析
# ---------------------------------------------------------------------------
def source_func(a, b):
    c = a + b
    return c * a


def demo_code_object():
    print("=" * 74)
    print("[2] 真实 code object 与 dis")
    print("=" * 74)
    co = source_func.__code__
    print(f"  co_varnames={co.co_varnames} co_names={co.co_names} co_consts={co.co_consts}")
    print(f"  co_nlocals={co.co_nlocals} co_stacksize={co.co_stacksize} "
          f"len(co_code)={len(co.co_code)} 字节")
    print("-- dis.get_instructions() 结构化视图(3.13 起 CACHE 不再单列,归入 cache_info)--")
    hist = {}
    for ins in dis.get_instructions(source_func):
        hist[ins.opname] = hist.get(ins.opname, 0) + 1
        print(f"  off={ins.offset:>3} line={str(ins.starts_line):<5} {ins.opname:<20}"
              f" arg={ins.arg} argval={ins.argval!r} cache={getattr(ins, 'cache_info', None)}")
    print(f"-- opcode 直方图:{hist}\n")


# ---------------------------------------------------------------------------
# §3 EXTENDED_ARG + CACHE
# ---------------------------------------------------------------------------
def demo_extended_arg_and_cache():
    print("=" * 74)
    print("[3] EXTENDED_ARG(突破 1 字节操作数)与 CACHE(内联缓存占位)")
    print("=" * 74)
    # 300 条独立赋值:co_consts 与 co_names 的索引都必然超过 255
    big = compile("\n".join(f"v{i} = {i}" for i in range(300)), "<big>", "exec")
    ins = list(dis.get_instructions(big))
    ext = [i for i in ins if i.opname == "EXTENDED_ARG"]
    loads = [i for i in ins if i.opname == "LOAD_CONST"]
    stores = [i for i in ins if i.opname == "STORE_NAME"]
    biggest = max(loads, key=lambda i: i.arg)
    prefix = biggest.offset - biggest.start_offset       # start_offset 含前缀
    print(f"  源码 300 条赋值 -> 常量/名字索引最大 {biggest.arg} > 255,"
          f"故出现 EXTENDED_ARG {len(ext)} 条")
    print(f"  例:arg={biggest.arg} 的 LOAD_CONST 带 {prefix} 个 EXTENDED_ARG 前缀"
          f"(start_offset={biggest.start_offset}, offset={biggest.offset})")
    print(f"  STORE_NAME 最大 arg={max(s.arg for s in stores)}(同一张 co_names 表)")
    print("  dis 原文:'at most three prefixal EXTENDED_ARG are allowed,"
          " forming an argument from two-byte to four-byte.'")
    # CACHE:BINARY_OP 在 3.11+ 带 1 个内联缓存槽(见 §2 的 cache_info),故槽数多于指令数
    f_ins = list(dis.get_instructions(source_func))
    slots = len(source_func.__code__.co_code) // 2
    cached = [i for i in f_ins if getattr(i, "cache_info", None)]
    print(f"  CACHE 例(复用 §2 的函数):co_code = {slots} 个 2 字节槽,"
          f"结构化指令 {len(f_ins)} 条,差额 {slots - len(f_ins)} 个即 CACHE 槽")
    print(f"  其中带内联缓存的指令:{[(i.opname, i.cache_info) for i in cached]}")
    print("  dis 原文:CACHE 'is used to mark extra space for the interpreter to cache"
          " useful data directly in the bytecode itself'(默认隐藏)\n")


# ---------------------------------------------------------------------------
# §4 PEP 659 自适应专用化模拟
# ---------------------------------------------------------------------------
class AdaptiveLoadAttr:
    """模拟 PEP 659 的 LOAD_ATTR 家族:自适应态持有 counter,归零后专用化;
    专用态持有饱和计数器,输入匹配则 +1,不匹配则 -1 并走通用路径,
    触底即把 opcode 换回自适应态(去优化)。
    """

    WARMUP, SATURATE, SLOT = 4, 2, "LOAD_ATTR_INSTANCE_VALUE"

    def __init__(self, name):
        self.name, self.state, self.counter = name, "LOAD_ATTR_ADAPTIVE", self.WARMUP
        self.stat = {"warmup": 0, "specialize": 0, "fast_hit": 0, "slow_path": 0, "deopt": 0}

    def execute(self, obj):
        if self.state == "LOAD_ATTR_ADAPTIVE":
            self.stat["warmup"] += 1
            if isinstance(obj, dict):
                self.counter -= 1
                if self.counter == 0:
                    self.state = self.SLOT
                    self.stat["specialize"] += 1
            return obj[self.name] if isinstance(obj, dict) else getattr(obj, self.name)
        if isinstance(obj, dict):
            self.stat["fast_hit"] += 1
            self.counter = min(self.counter + 1, AdaptiveLoadAttr.SATURATE)
            return obj[self.name]
        self.stat["slow_path"] += 1
        value = getattr(obj, self.name)
        self.counter -= 1
        if self.counter <= 0:
            self.state, self.counter = "LOAD_ATTR_ADAPTIVE", self.WARMUP
            self.stat["deopt"] += 1
        return value


class Bag:                     # 完全不同的接收者类型:实例属性走 slot,不是 dict
    x = 100


def demo_specialization():
    print("=" * 74)
    print("[4] PEP 659 专用化自适应解释器模拟(LOAD_ATTR 家族)")
    print("=" * 74)
    slot = AdaptiveLoadAttr("x")
    print("  阶段 A:连续 dict 输入(预热 -> 专用化 -> 快路径命中)")
    for i in range(6):
        print(f"    #{i + 1} dict(x={i}) -> {slot.execute({'x': i})};state={slot.state}")
    print("  阶段 B:换接收者类型 Bag(),走慢路径并让饱和计数器衰减到去优化")
    for i in range(4):
        print(f"    #{i + 1} Bag().x -> {slot.execute(Bag())};"
              f"state={slot.state} counter={slot.counter}")
    print(f"  统计:{slot.stat}")
    print("  PEP 659 原文:'Each adaptive instruction periodically attempts to specialize"
          " itself.' / 'If the counter reaches the minimum value, the instruction is"
          " de-optimized by simply replacing its opcode with the adaptive version.'")
    print("  (预热次数与饱和阈值是教学取值,PEP 只规定机制不规定具体数)\n")


# ---------------------------------------------------------------------------
# §5 静态栈深度 vs co_stacksize
# ---------------------------------------------------------------------------
def demo_stack_effect():
    print("=" * 74)
    print("[5] 用 dis.stack_effect 估算栈深,与编译器给出的 co_stacksize 对照")
    print("=" * 74)
    depth = peak = 0
    for ins in dis.get_instructions(source_func):
        try:
            depth = max(0, depth + dis.stack_effect(ins.opcode, ins.arg or 0))
        except ValueError:
            continue                                  # 极少数 opcode 不接受 oparg
        peak = max(peak, depth)
    print(f"  直线累加峰值栈深 = {peak};编译器 co_stacksize = "
          f"{source_func.__code__.co_stacksize}")
    print("  线性算法遇到分支/循环会低估(未枚举全部控制流路径);真实编译器在基本块上"
          "做数据流分析。")
    print("  dis.stack_effect 的 jump 参数正为此:jump=True/False 取跳转/不跳转分支,"
          "None 时返回两者最大值。")


def main():
    print(f"Python {sys.version}")
    demo_mini_vm()
    demo_code_object()
    demo_extended_arg_and_cache()
    demo_specialization()
    demo_stack_effect()
    print("\n全部章节执行完毕。")


if __name__ == "__main__":
    main()
