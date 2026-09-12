#!/usr/bin/env python3
"""ELF PLT/GOT 惰性绑定(lazy binding)完整流程模拟器。

依据 Ian Lance Taylor "Linkers part 4: Shared Libraries"(airs.com/archives/41)
描述的 i386 经典结构(思想与 x86-64 相同):

  PLT[0]   pushl 4(%ebx)      ; push 动态链接器标识(GOT[1])
           jmp  *8(%ebx)      ; 跳动态链接器入口(GOT[2])
  PLT[n]   jmp  *offset(%ebx) ; 经 GOT[n+2] 跳转(初始指向下一条指令)
           pushl $index       ; 把自己的重定位索引压栈
           jmp  PLT[0]        ; 进解析器

  GOT[0]=.dynamic 地址  GOT[1]=link_map  GOT[2]=_dl_runtime_resolve
  GOT[3..]=每个 PLT 条目一个槽,初始指向本 PLT 条目的第二条指令

JMP_SLOT 重定位(elf(5): 由 DT_JMPREL 定位、DT_PLTRELSZ 计大小)逐个
对应 GOT 槽;首次调用时动态链接器查符号、回填 GOT,此后直达。
"""
import random

SYM = ["printf", "malloc", "strcmp"]  # 模拟的动态符号(对应 .rela.plt 顺序)
PLT0 = "PLT[0]"


def plt_entry(i):
    """返回 PLT 第 i 个条目的三条'指令'(地址用 PLT[i] 表示)。"""
    got_slot = f"GOT[{i + 3}]"           # GOT[0..2] 被 PLT0 占用
    return [f"jmp  *{got_slot}",         # ① 间接跳
            f"push ${i}",                # ② 压重定位索引
            f"jmp  {PLT0}"]              # ③ 进解析器


class Machine:
    """极简机器:GOT 是全局状态,模拟函数调用穿过 PLT 的路径。"""

    def __init__(self):
        self.resolved = 0          # 动态链接器被叫醒的次数(性能度量!)
        self.got = {}              # 槽 → 目标(字符串)
        # GOT[1]/GOT[2]:PLT0 专用,动态链接器在加载时填好
        self.got["GOT[1]"] = "link_map"
        self.got["GOT[2]"] = "_dl_runtime_resolve"
        # GOT[3..] 初始指向"本 PLT 条目的第二条指令"——惰性绑定的精髓
        for i in range(len(SYM)):
            self.got[f"GOT[{i + 3}]"] = f"PLT[{i}]+1(push $index)"

    def call(self, sym):
        i = SYM.index(sym)
        trace = [f"call {sym} → 进入 PLT[{i}]"]
        # ① jmp *GOT[i+3]
        target = self.got[f"GOT[{i + 3}]"]
        if target.startswith(f"PLT[{i}]+1"):
            # 未绑定:落到第二条指令
            trace.append(f"  ① jmp *GOT[{i + 3}] → {target}(未解析,落回本条目)")
            trace.append(f"  ② push ${i} ── 把重定位索引交给解析器")
            trace.append(f"  ③ jmp PLT[0] → push GOT[1](link_map) + jmp *GOT[2]")
            # _dl_runtime_resolve:用 (link_map, index) 查 .rela.plt → 符号名
            trace.append(f"  ④ _dl_runtime_resolve(link_map, reloc[{i}])"
                         f" → 查 .dynsym 得 '{sym}' 地址")
            self.got[f"GOT[{i + 3}]"] = f"libc:{sym}"   # 回填 GOT —— 永久生效
            self.resolved += 1
            trace.append(f"  ⑤ 回填 GOT[{i + 3}] = libc:{sym},本次跳真实函数")
        else:
            trace.append(f"  ① jmp *GOT[{i + 3}] → {target}(已解析,直达)")
        return "\n".join(trace)


def demo():
    m = Machine()
    print("== 首次调用 printf(全流程 5 步)==")
    print(m.call("printf"))
    print("\n== 第二次调用 printf(一步直达)==")
    print(m.call("printf"))
    print("\n== 再调用 strcmp 一次(首次解析)==")
    print(m.call("strcmp"))
    print("\n== 第二次 strcmp + 已解析的 printf ==")
    print(m.call("strcmp"))
    print(m.call("printf"))
    print(f"\n动态链接器被叫醒次数 = {m.resolved} / 总调用 5 次"
          f" —— 惰性绑定只解析'真正被调过'的符号")

    # BIND_NOW 对比:加载期全量解析
    n = len(SYM)
    print(f"\n若 LD_BIND_NOW / DT_BIND_NOW:启动时解析全部 {n} 个,启动变慢,"
          f"但首次调用不再有解析开销(RELR/FULL RELRO 的前提)")


if __name__ == "__main__":
    demo()
