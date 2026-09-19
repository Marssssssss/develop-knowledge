#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TLS 四种访问模型自检：四条口径必须算出同一个地址；各自的开销必须不同。

运行：python tls_check.py

核心判据不是「能算出地址」，而是 **GD / LD / IE / LE 四条路径在同一变量上
必须给出同一个地址** —— 只要有一条不等，就说明某个模型的偏移折算错了。
"""

import sys

from tls_models import (TlsModule, Thread, Executable, MODEL_COST,
                        available_models, EXEC_MODULE_ID)

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def raises(fn, exc, label):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        FAIL.append("%s:应抛 %s,实抛 %r" % (label, exc.__name__, e))
        return False
    FAIL.append("%s:应抛 %s 却没有" % (label, exc.__name__))
    return False


def main():
    print("== 1. §3.1 模块编号：可执行文件必须是 1 ==")
    check(EXEC_MODULE_ID == 1, "可执行文件的模块号固定为 1")
    exe_mod = TlsModule(1, "a.out", block_size=0x40,
                        offsets={"errno_like": 0x00, "counter": 0x20})
    so_mod = TlsModule(2, "libhelper.so", block_size=0x20,
                       offsets={"helper_buf": 0x08})
    check(exe_mod.id == 1 and so_mod.id != 1, "只有可执行文件拿到 1，其它各不相同")

    print("== 2. §3.2 延迟分配：第一次访问才 allocate_tls ==")
    exe = Executable([exe_mod, so_mod])
    exe.register_static_block(exe_mod)
    t = exe.new_thread(0)
    check(t.alloc_count == 0, "线程诞生时一个动态 TLS 块都没分配")
    check(t.is_static(exe_mod), "可执行文件的 TLS 块属于静态 TLS 区")
    check(t.is_static(so_mod) is False, "dlopen 进来的模块不属静态区")
    addr = t.tls_get_addr(so_mod, so_mod.offset_of("helper_buf"))
    check(t.alloc_count == 1, "__tls_get_addr 第一次触发分配（动态模块）")
    check(addr == t.dtv[2] + 0x08, "返回值 = 块基址 + 块内偏移")
    again = t.tls_get_addr(so_mod, so_mod.offset_of("helper_buf"))
    check(again == addr and t.alloc_count == 1,
          "第二次访问不再分配（延迟分配是一次性的）")
    check(t.dtv[2] == t.blocks[2]["addr"], "dtv[m] 记录的就是块地址")
    check(t.blocks[2]["size"] == 0x20, "块大小来自模块的 TLS template")
    check(t.tls_get_addr(exe_mod, 0) == t.tp + exe.static_delta[1],
          "静态模块的块基址 = TP + delta，不触发分配")

    print("== 3. 线程间隔离：同一个变量在两个线程里是两个地址 ==")
    t2 = exe.new_thread(1)
    addr2 = t2.tls_get_addr(so_mod, so_mod.offset_of("helper_buf"))
    check(addr2 != addr, "不同线程拿到不同地址")
    check(addr2 - t2.dtv[2] == addr - t.dtv[2], "块内偏移一致")
    # 真读写一遍，确认不是同一块内存
    t.blocks[2]["mem"][0x08] = 0x11
    t2.blocks[2]["mem"][0x08] = 0x22
    check(t.blocks[2]["mem"][0x08] != t2.blocks[2]["mem"][0x08],
          "写互不可见")
    check(t.tp != t2.tp, "两个线程各有自己的线程指针（各自的 TCB）")

    print("== 4. 四种模型必须算出同一个地址 ==")
    # IE 模型的 GOT 槽由动态链接器在启动时填成 TPOFF64
    exe.startup_resolve_tpoff(exe_mod, "counter",
                              exe.offset_from_tp(exe_mod, "counter"))
    tp_delta = exe.offset_from_tp(exe_mod, "counter")
    check(tp_delta < 0, "TPOFF 是相对线程指针的**负向**偏移：%d" % tp_delta)

    gd = exe.gd_access(t, exe_mod, "counter")
    base = t.tls_get_addr(exe_mod, 0)
    ld = exe.ld_access(t, exe_mod, "counter", base=base)
    ie = exe.ie_access(t, exe_mod, "counter")
    le = exe.le_access(t, exe_mod, "counter")
    check(gd == ld == ie == le,
          "GD/LD/IE/LE 四条路径同一地址：gd=0x%x ld=0x%x ie=0x%x le=0x%x"
          % (gd, ld, ie, le))
    check(gd == t.tls_get_addr(exe_mod, exe_mod.offset_of("counter")),
          "且与 __tls_get_addr 直接算的结果一致")
    check(le == t.tp + tp_delta, "LE = TP + immediate(x@tpoff)")
    check(ie == t.tp + exe.got[(1, "counter")], "IE = TP + GOT 槽里的 TPOFF")
    check(ld == base + 0x20, "LD = 模块基址 + @dtpoff")

    print("== 5. §4.2 Local Dynamic：同一模块多变量只付一次调用 ==")
    # 换一个尚未分配过的新模块，才能量出「取基址触发了几次分配」
    so2 = TlsModule(3, "libplugin.so", block_size=0x10,
                    offsets={"flag": 0x04, "seq": 0x08})
    before_alloc = t.alloc_count
    b1 = t.tls_get_addr(so2, 0)
    check(t.alloc_count == before_alloc + 1, "取模块基址触发一次分配")
    a1 = exe.ld_access(t, so2, "flag", base=b1)
    a2 = exe.ld_access(t, so2, "seq", base=b1)
    check(t.alloc_count == before_alloc + 1,
          "之后每个变量只是「基址 + dtpoff」，不再分配")
    check(a1 == b1 + 0x04 and a2 == b1 + 0x08, "@dtpoff 只是相对基址加一个偏移")
    # 文档原话：additional variable 只需 three new instructions，不再要 GOT 槽
    check(t.tls_get_addr(so2, 0) == b1, "同线程重复取基址是幂等的")
    # 同一模块的两个变量，基址只算一次 —— 这正是 LD 相对 GD 的全部收益
    check(exe.gd_access(t, so2, "flag") == a1 and exe.gd_access(t, so2, "seq") == a2,
          "GD 每次访问都重算，结果与 LD 一致（只是更贵）")

    print("== 6. 模型可用性：LE 最严、GD/LD 最宽 ==")
    check(available_models(True, True) == ["GD", "IE", "LD", "LE"],
          "可执行文件 + 启动时已知：四种全可用")
    check(available_models(False, True) == ["GD", "IE", "LD"],
          "共享库 + 启动时已知：没有 LE")
    check(available_models(False, False) == ["GD", "LD"],
          "dlopen 进来的模块：只剩动态模型")
    check("LE" not in available_models(True, False),
          "可执行文件但非启动时已知也不给 LE")

    print("== 7. §4 各模型的开销口径 ==")
    check(MODEL_COST["GD"]["got_slots_per_sym"] == 2,
          "GD：每个符号两个连续 GOT 槽（DTPMOD64 + DTPOFF64）")
    check(MODEL_COST["GD"]["calls_per_sym"] == 1,
          "GD：每次访问都要调一次 __tls_get_addr")
    check(MODEL_COST["LD"]["got_slots_per_sym"] == 0,
          "LD：每个额外变量 0 个 GOT 槽（复用模块的 tls index）")
    check(MODEL_COST["LD"]["calls_per_sym"] == 0 and
          MODEL_COST["IE"]["calls_per_sym"] == 0 and
          MODEL_COST["LE"]["calls_per_sym"] == 0,
          "LD/IE/LE 都不需要在访问路径上调用 __tls_get_addr")
    check(MODEL_COST["IE"]["got_slots_per_sym"] == 1,
          "IE：一个 GOT 槽，启动时被填成 TPOFF64")
    check(MODEL_COST["LE"]["got_slots_per_sym"] == 0,
          "LE：连 GOT 都不需要，偏移直接进 immediate")
    check(MODEL_COST["LE"]["notes"] != MODEL_COST["IE"]["notes"],
          "LE 与 IE 的区别在偏移的准备时机（链接期 vs 启动期）")

    print("== 8. IE 的 GOT 槽必须先被填；非静态模块没有 x@tpoff ==")
    raises(lambda: exe.ie_access(t, so_mod, "helper_buf"), KeyError,
           "未 resolve 的 IE 槽")
    raises(lambda: exe.offset_from_tp(so_mod, "helper_buf"), KeyError,
           "非静态模块没有 x@tpoff")
    exe.startup_resolve_tpoff(so_mod, "helper_buf", -0x18)
    check(exe.ie_access(t, so_mod, "helper_buf") == t.tp - 0x18,
          "填槽后 IE 直接用 TP + 槽值")

    print("== 9. LD 不生成 DTPOFF64：只有 DTPMOD64 那个槽 ==")
    # 文档 §4.2.6：「The linker will create only one relocation for the object,
    # R_X86_64_DTPMOD64. The R_X86_64_DTPOFF64 relocation is not necessary.」
    # 用一张小表直接表达这个差别，避免只写在注释里。
    relocs = {
        "GD": ["R_X86_64_TLSGD", "R_X86_64_DTPMOD64", "R_X86_64_DTPOFF64",
               "R_X86_64_PLT32"],
        "LD": ["R_X86_64_TLSLD", "R_X86_64_DTPMOD64", "R_X86_64_DTPOFF32",
               "R_X86_64_PLT32"],
        "IE": ["R_X86_64_GOTTPOFF", "R_X86_64_TPOFF64"],
        "LE": ["R_X86_64_TPOFF32"],
    }
    check("R_X86_64_DTPOFF64" in relocs["GD"] and
          "R_X86_64_DTPOFF64" not in relocs["LD"],
          "GD 需要模块+偏移两个槽，LD 只需要模块那一个")
    check(relocs["LE"] == ["R_X86_64_TPOFF32"], "LE 只剩一条静态重定位")
    check(len(relocs["GD"]) > len(relocs["IE"]) > len(relocs["LE"]) and
          len(relocs["LD"]) > len(relocs["IE"]),
          "重定位条目数随模型「静态化」而减少")
    check(len(relocs["GD"]) == len(relocs["LD"]),
          "GD 与 LD 的条目数相同（差别在 DTPOFF64 换成 DTPOFF32）")

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
