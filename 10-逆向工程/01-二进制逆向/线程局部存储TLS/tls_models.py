#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ELF 线程局部存储（TLS）：四种访问模型的地址口径。

数据来源（本轮实测下载并提取正文）：
  Ulrich Drepper, "ELF Handling For Thread-Local Storage", Version 0.20, 2005-12-21
    §3 Run-time Handling of TLS —— dtvt / tls_get_addr / 延迟分配 / 两种 variant
    §4 TLS Access Models —— x86-64 四种模型的指令序列与重定位
    https://www.uclibc.org/docs/tls.pdf
  用到的重定位编号取自 System V AMD64 psABI Draft 0.99.6 Table 4.10。

四类访问的口径（都指向「当前线程的那个变量」）：

    GD  __tls_get_addr(tls_index{x})                每符号 2 个 GOT 槽 + 1 次调用
    LD  base = __tls_get_addr(tls_index{module});   同模块多变量共用一次调用
        base + x@dtpoff
    IE  TP + *(GOT[x])                              GOT 槽在启动期被填成 TPOFF
    LE  TP + immediate(x@tpoff)                     链接期定死，无任何运行时重定位
"""

# ------------------------------------------------------- 模块编号与 DTV

# 文档 §3.1 原文：「the module reference as an integer starting with 1 (one).
# Only the executable itself must receive a fixed number, 1 (one)」。
EXEC_MODULE_ID = 1


class TlsModule(object):
    """一个已加载模块的 TLS template：块大小 + 各变量在块内的偏移。"""

    def __init__(self, mid, name, block_size, offsets=None):
        self.id = mid
        self.name = name
        self.block_size = block_size
        self.offsets = dict(offsets or {})

    def offset_of(self, sym):
        return self.offsets[sym]


class Thread(object):
    """一个线程的 TLS 状态：dtv[m] -> 该 TLS 块的起始地址。

    dtv 的第 0 项是 generation counter（真实实现用它判断 dtv 是否需要扩容），
    本 demo 只建模第 1 项之后的模块槽。

    静态 TLS 区（可执行文件的块）在线程创建时就已经存在，地址 = TP + delta，
    因此**不参与延迟分配**；其余模块按需 allocate_tls。
    """

    UNALLOCATED = None      # 延迟分配的哨兵

    def __init__(self, tid, thread_pointer, static_blocks=None,
                 next_block_base=0x7F0000000000):
        self.tid = tid
        self.tp = thread_pointer   # 线程指针（x86-64 上取 %fs:0）
        self.dtv = {0: 1}          # dtv[0] = generation
        self.blocks = {}           # mid -> dict(addr, size, mem)
        self.static_blocks = dict(static_blocks or {})
        for mid, delta in self.static_blocks.items():
            self.dtv[mid] = self.tp + delta
        self.next_base = next_block_base + tid * 0x1000000
        self.alloc_count = 0

    def is_static(self, module):
        return module.id in self.static_blocks

    def allocate_tls(self, module):
        """allocate_tls(m)：真的把内存拿出来，并写进 dtv[m]。"""
        addr = self.next_base
        size = module.block_size
        self.next_base += size + 0x1000          # 块之间留一页，便于断言区分
        self.blocks[module.id] = {"addr": addr, "size": size,
                                  "mem": bytearray(size)}
        self.dtv[module.id] = addr
        self.alloc_count += 1
        return addr

    def tls_get_addr(self, module, offset):
        """文档 §3.2 给出的原型：

            void *__tls_get_addr (size_t m, size_t offset) {
              char *tls_block = dtv[thread_id][m];
              if (tls_block == UNALLOCATED_TLS_BLOCK)
                tls_block = dtv[thread_id][m] = allocate_tls (m);
              return tls_block + offset;
            }
        """
        block = self.dtv.get(module.id, Thread.UNALLOCATED)
        if block is Thread.UNALLOCATED:
            block = self.allocate_tls(module)
        return block + offset


# ------------------------------------------------------------ 访问模型

class Executable(object):
    """把「链接期 / 启动期已经定下的信息」与「运行时状态」分开持有。

    静态 TLS 区只有可执行文件的块能进（动态链接器在线程创建时就会把它铺好）；
    IE 模型依赖的 GOT 槽，其 TPOFF64 由动态链接器在**程序启动时**填好。
    """

    def __init__(self, modules, tcb_base=0x7FFFFFFF0000, tcb_stride=0x100000):
        self.modules = dict((m.id, m) for m in modules)
        self.tcb_base = tcb_base
        self.tcb_stride = tcb_stride
        self.static_delta = {}   # mid -> 块起始相对 TP 的偏移（静态 TLS 区）
        self.got = {}            # (mid, sym) -> 相对 TP 的偏移（TPOFF 值）

    def register_static_block(self, module):
        """把模块放进静态 TLS 区。

        本文档 §3 区分了 variant I / variant II 两种布局，x86-64 属后者：
        TLS 块位于线程指针**之下**，所以块起始相对 TP 的偏移是 -block_size。
        """
        self.static_delta[module.id] = -module.block_size

    def new_thread(self, tid):
        """每个线程有独立的 TCB，也就有独立的线程指针。"""
        return Thread(tid, self.tcb_base + tid * self.tcb_stride,
                      static_blocks=self.static_delta)

    def startup_resolve_tpoff(self, module, sym, offset_from_tp):
        """动态链接器在启动时处理 R_X86_64_TPOFF64，把偏移写进 GOT 槽。"""
        self.got[(module.id, sym)] = offset_from_tp

    # --- 四种模型 ------------------------------------------------------

    def gd_access(self, thread, module, sym):
        """General Dynamic：x@tlsgd → __tls_get_addr(tls_index{x})。

        GOT 需要**两个连续槽**（DTPMOD64 / DTPOFF64），且每次访问都要调一次
        __tls_get_addr —— 四种模型里最贵，也是唯一对 dlopen 完全通用的。
        """
        return thread.tls_get_addr(module, module.offset_of(sym))

    def ld_access(self, thread, module, sym, base=None):
        """Local Dynamic：先拿到「本模块 TLS 块的基址」，之后按 dtpoff 取值。

        x1@tlsld 只需一个 GOT 槽（DTPMOD64），R_X86_64_DTPOFF64 不必生成：
            0x00 leaq x1@tlsld(%rip),%rdi
            0x07 call tls_get_addr@plt
            0x10 leaq x1@dtpoff(%rax),%rcx
        """
        if base is None:
            base = thread.tls_get_addr(module, 0)
        return base + module.offset_of(sym)

    def ie_access(self, thread, module, sym):
        """Initial Exec：%fs:0 取线程指针，再 addq x@gottpoff(%rip)。

        GOT 槽里是启动时算好的相对 TP 的偏移，运行时不再调用 __tls_get_addr。
        """
        key = (module.id, sym)
        if key not in self.got:
            raise KeyError("IE 模型的 GOT 槽尚未被动态链接器填入: %s" % (key,))
        return thread.tp + self.got[key]

    def le_access(self, thread, module, sym):
        """Local Exec：偏移作为 immediate 编码进指令，无运行时重定位。

            0x00 movq %fs:0,%rax
            0x09 leaq x@tpoff(%rax),%rax      R_X86_64_TPOFF32
        """
        return thread.tp + self.offset_from_tp(module, sym)

    def offset_from_tp(self, module, sym):
        """x@tpoff：链接器静态算出的、相对线程指针的偏移。

        文档 §4.4：「is only an addition of the offset which is available as
        an immediate value to the thread pointer」。
        """
        if module.id not in self.static_delta:
            raise KeyError("非静态 TLS 区的模块没有 x@tpoff: %s" % module.name)
        return self.static_delta[module.id] + module.offset_of(sym)


# ------------------------------------------------------- 各模型的开销口径

MODEL_COST = {
    "GD": {"got_slots_per_sym": 2, "calls_per_sym": 1,
           "notes": "唯一对 dlopen 通用：每个符号一次调用 + 2 个 GOT 槽"},
    "LD": {"got_slots_per_sym": 0, "calls_per_sym": 0,
           "notes": "同模块多变量最省：一次调用 + 之后每变量 3 条指令"},
    "IE": {"got_slots_per_sym": 1, "calls_per_sym": 0,
           "notes": "启动期一次性把 TPOFF64 填进 GOT"},
    "LE": {"got_slots_per_sym": 0, "calls_per_sym": 0,
           "notes": "最快：偏移直接进 immediate，仅限可执行文件"},
}


def available_models(module_is_executable, known_at_startup):
    """哪些模型可用（按严格程度递增）。

    LE：偏移必须**链接期**定死 → 只能在可执行文件里。
    IE：偏移必须**启动期**定死 → 变量所属模块必须在程序启动时已加载。
    GD / LD：剩下的一切场景（dlopen 进来的模块）只能用动态模型。
    LD 相对 GD 的额外限制：只适用于本模块内的变量（@dtpoff 的前提）。
    """
    out = ["GD", "LD"]
    if known_at_startup:
        out.append("IE")
    if module_is_executable and known_at_startup:
        out.append("LE")
    return sorted(out)
