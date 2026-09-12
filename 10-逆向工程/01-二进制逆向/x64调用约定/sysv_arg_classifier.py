#!/usr/bin/env python3
"""x86-64 System V 参数分类算法模拟器(教育目的)。

psABI §3.2.3 的参数传递流程(简化到整数/浮点/小结构):
  1. 逐参分类:INTEGER(整数/指针/≤8B 结构)/ SSE(double/float)
  2. INTEGER 依序占用 rdi rsi rdx rcx r8 r9,溢出者入栈(右到左)
  3. SSE 依序占用 xmm0-xmm7(与 INTEGER 序列并行计数,互不挤占)
  4. 结构按 8B(eightbyte)分片归类后可拆进多寄存器(本模拟不含 MEMORY 类大结构)
  5. 可变参数:入口处 %al = 使用了几个 SSE 寄存器(上界)

逆向价值:给静态/动态分析中的函数"猜签名"提供规则依据。
"""
from dataclasses import dataclass, field

INT_REGS = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]
SSE_REGS = [f"xmm{i}" for i in range(8)]


@dataclass
class Arg:
    kind: str          # 'int' | 'float' | 'struct'
    name: str
    eightbytes: list = field(default_factory=list)  # 结构的 8B 分片类别

    def classify(self):
        if self.kind == "int":
            return ["INTEGER"]
        if self.kind == "float":
            return ["SSE"]
        return self.eightbytes  # 如 (int,double) → ['INTEGER','SSE']


def call(args):
    """返回 (布局表, %al 值)。INTEGER 与 SSE 各有独立 8/6 个槽位。"""
    int_iter = iter(INT_REGS)
    sse_iter = iter(SSE_REGS)
    layout, stack, sse_used = [], [], 0
    for a in args:
        for cls in a.classify():
            if cls == "INTEGER":
                reg = next(int_iter, None)
                if reg is None:
                    stack.append((a.name, cls))
                else:
                    layout.append((a.name, cls, reg))
            else:
                reg = next(sse_iter, None)
                sse_used += 1
                if reg is None:
                    stack.append((a.name, cls))
                else:
                    layout.append((a.name, cls, reg))
    layout += [(n, c, "stack") for n, c in stack]
    return layout, min(sse_used, 8)  # %al 是"上界"而非精确值


def show(args):
    layout, al = call(args)
    print(f"调用 f({', '.join(a.name for a in args)}):")
    for name, cls, loc in layout:
        print(f"    {name:<10} {cls:<8} → {loc}")
    if any(l[2] == "stack" for l in layout):
        print("    (栈参数按右到左压栈)")
    print(f"    入口 %al = {al}(可变参数时告知 SSE 寄存器用量上界)\n")


if __name__ == "__main__":
    # psABI Figure 3.5/3.6 经典例子的近似复现
    show([Arg("int", "e"), Arg("int", "f"),
          Arg("struct", "s", ["INTEGER", "SSE"]),   # struct {int a,b; double d;}
          Arg("int", "g"), Arg("int", "h"),
          Arg("float", "ld"), Arg("float", "m"), Arg("float", "n"),
          Arg("int", "i"), Arg("int", "j"), Arg("int", "k")])
    show([Arg("int", "a"), Arg("int", "b"), Arg("int", "c"),
          Arg("int", "d"), Arg("int", "e"), Arg("int", "f"),
          Arg("int", "g"), Arg("int", "h")])  # 8 个整数: 6 寄存器 + 2 栈
    show([Arg("float", "x"), Arg("float", "y")])   # 2 个浮点: xmm0/xmm1
