"""
Seccomp-BPF 程序生成器与教学演示(无 root,生成 BPF 字节流)。

参考资料(已实际阅读,见 README):
  kernel.org userspace-api/seccomp_filter.html
  man7 seccomp(2)             — 6 flags 与 SECCOMP_SET_MODE_FILTER
  man7 prctl(2) PR_SET_SECCOMP

用法:
  python3 seccomp_demo.py dump whitelist "read,write,exit,exit_group" 64
  python3 seccomp_demo.py dump blacklist "rmmod,init_module" 32
  python3 seccomp_demo.py arch-info                         # 列出常见 arch 值
  python3 seccomp_demo.py actions-info                      # 5 种 RET 值十六进制表
"""

import struct
import sys

# syscall 号(按 linux/x86_64 通用)
SYSCALL_NAMES_X86_64 = {
    0: "read", 1: "write", 2: "open", 3: "close", 9: "mmap", 10: "mprotect",
    12: "brk", 13: "rt_sigaction", 14: "rt_sigprocmask", 15: "rt_sigreturn",
    21: "access", 39: "getpid", 56: "clone", 57: "fork", 59: "execve",
    60: "exit", 78: "getdents", 87: "unlink", 217: "getdents64",
    231: "exit_group", 233: "madvise",
}

# SECCOMP_RET_* 来自 kernel.org seccomp_filter.html §"Return values"
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_KILL_THREAD  = 0x00000000
SECCOMP_RET_TRAP         = 0x00030000
SECCOMP_RET_ERRNO        = 0x00050000
SECCOMP_RET_LOG          = 0x7FC00000
SECCOMP_RET_USER_NOTIF   = 0x7FC00000  # alias of LOG
SECCOMP_RET_ALLOW        = 0x7FFF0000


def emit(code: int, jt: int = 0, jf: int = 0, k: int = 0) -> bytes:
    """BPF 指令编码 — `struct sock_filter { __u16 code; __u8 jt, jf; __u32 k; }`。

    Linux kernel.seccomp_data.arch 是 32-bit 无符号(AUDIT_ARCH_*),需先把 k 当无符号;但
    SECCOMP_RET_KILL_PROCESS = 0x80000000 也只有 32 位正整数范围。这里 emit 用 "<HBBi" 会
    把超过 INT32_MAX 的值报错,故手动截断 k = k & 0xFFFFFFFF 并以 signed 还原。
    """
    # 注意:k 在 BPF 语境下是 32 位有符号(`int k`),但 uvalue 如 audit arch 是无符号 32-bit
    # 在 SECCOMP_RET_* 范围内(0x80000000~0x7FFF0000)int32 边界上要小心
    k_unsigned = k & 0xFFFFFFFF
    # 用 "I" 编码 + signed int32 截断(对负数 k 也兼容,k & 0x80000000 时按 二补码 还原)
    if k_unsigned & 0x80000000:
        k_s = k_unsigned - 0x100000000
    else:
        k_s = k_unsigned
    return struct.pack("<HBBi", code & 0xFFFF, jt & 0xFF, jf & 0xFF, k_s)


def gen_whitelist(allowed_syscall_names: list[str], arch_aarch64: bool = False) -> list[bytes]:
    """生成"白名单"BPF 程序:仅允许指定 syscall 号;其余 KILL_PROCESS。

    Layout(参考 Mozilla Seccomp BPF Macro):
      1. Load arch,检查
      2. Load syscall nr,A
      3. 对每个允许 nr 做 BPF_JUMP+BPF_JEQ,命中 ret ALLOW
      4. 末尾 ret KILL_PROCESS
    """
    # Linux x86_64 syscall nr 解析(简化;仅给 demo 用)
    nr_by_name = {v: k for k, v in SYSCALL_NAMES_X86_64.items()}
    if arch_aarch64:
        # aarch64 不同号;这里仅做示意
        nr_by_name = {"read": 63, "write": 64, "exit": 93, "exit_group": 94}

    nrs = []
    for name in allowed_syscall_names:
        if name not in nr_by_name:
            raise ValueError(f"unknown syscall in current arch: {name}")
        nrs.append(nr_by_name[name])
    nrs.sort()

    BPF_LD = 0x00
    BPF_W = 0x00
    BPF_ABS = 0x20
    BPF_JMP = 0x05
    BPF_JEQ = 0x10
    BPF_K = 0x00
    BPF_RET = 0x06

    out = []
    # Load arch(AUDIT_ARCH_X86_64 = 0xC000003E 见 linux/audit.h)
    out.append(emit(BPF_LD+BPF_W+BPF_ABS, k=4))           # offsetof(seccomp_data, arch)
    out.append(emit(BPF_JMP+BPF_JEQ+BPF_K, jt=1, jf=0, k=0xC000003E))
    out.append(emit(BPF_RET+BPF_K, k=SECCOMP_RET_KILL_PROCESS))
    # Load syscall nr
    out.append(emit(BPF_LD+BPF_W+BPF_ABS, k=0))           # offsetof(seccomp_data, nr)
    # Linear search:每个 allow 行是 1 JEQ + 1 RET(命中即 ALLOW,跳过下一步)
    for nr in nrs:
        out.append(emit(BPF_JMP+BPF_JEQ+BPF_K, jt=0, jf=1, k=nr))   # jf=1 → 跳到下一个
        out.append(emit(BPF_RET+BPF_K, k=SECCOMP_RET_ALLOW))
    # 不匹配 → KILL
    out.append(emit(BPF_RET+BPF_K, k=SECCOMP_RET_KILL_PROCESS))
    return out


def assemble(bpf_program: list[bytes]) -> bytes:
    """串联 BPF 指令为 flat 字节(模拟 sock_fprog 内存布局)。"""
    return b"".join(bpf_program)


# ---------- 演示 CLI ----------

def dump_action_table():
    print("# SECCOMP_RET_* 值(kernel.org userspace-api/seccomp_filter.html §Return values)")
    table = [
        ("SECCOMP_RET_KILL_PROCESS", SECCOMP_RET_KILL_PROCESS, "整个进程退出,exit=SIGSYS"),
        ("SECCOMP_RET_KILL_THREAD",  SECCOMP_RET_KILL_THREAD,  "仅线程退出"),
        ("SECCOMP_RET_TRAP",         SECCOMP_RET_TRAP,         "发 SIGSYS,handler 可模拟"),
        ("SECCOMP_RET_ERRNO",        SECCOMP_RET_ERRNO,        "返回自定义 errno(低 16 位)"),
        ("SECCOMP_RET_USER_NOTIF",   SECCOMP_RET_USER_NOTIF,   "用户态 fd 收到通知(5.0+)"),
        ("SECCOMP_RET_LOG",          SECCOMP_RET_LOG,          "允许但 audit log(4.14+)"),
        ("SECCOMP_RET_ALLOW",        SECCOMP_RET_ALLOW,        "允许"),
    ]
    for name, v, desc in table:
        print(f"  {name:24s} = {v:#010x}  // {desc}")
    print()
    print("# 多个 filter 同时生效时,取最高优先级:KILL_PROCESS > KILL_THREAD > TRAP > ERRNO > LOG/NOTIF > ALLOW")


def dump_arch_info():
    print("# seccomp_data.arch 常见值(linux/audit.h AUDIT_ARCH_*)")
    arches = [
        ("AUDIT_ARCH_X86_64",  0xC000003E, "64-bit x86 (Linux)"),
        ("AUDIT_ARCH_I386",    0x40000003, "32-bit x86"),
        ("AUDIT_ARCH_AARCH64", 0xC00000B7, "64-bit ARM"),
        ("AUDIT_ARCH_ARM",     0x40000028, "32-bit ARM"),
        ("AUDIT_ARCH_RISCV64", 0xC00000F3, "RISC-V 64-bit"),
        ("AUDIT_ARCH_PPC64",   0xC0000015, "PowerPC 64"),
    ]
    for n, v, d in arches:
        print(f"  {n:18s} = {v:#010x}  // {d}")
    print()
    print("# 同一条 BPF 程序跨 arch 时需重新 encode(由于 syscall 号不同)")
    print("# 正确做法:在 BPF 程序首部校验 arch,再分发到对应分支")


def cmd_dump_whitelist(args):
    if len(args) < 2:
        print("usage: dump whitelist <allowed_syscall_csv> [arch=64]")
        return
    allowed = [s.strip() for s in args[0].split(",") if s.strip()]
    arch_aarch64 = "aarch" in (args[1] if len(args) > 1 else "")
    print(f"# BPF 程序 for 白名单 ({arch_aarch64 and 'aarch64' or 'x86_64'}):")
    print(f"# 允许 syscall: {allowed}")
    print()
    print("/* struct sock_filter filter[] = { */")
    for i, insn_bytes in enumerate(gen_whitelist(allowed, arch_aarch64)):
        # 反解为可读
        code, jt, jf, k = struct.unpack("<HBBi", insn_bytes)
        label = bp_label(code, k)
        print(f"    BPF_INSN({label:<22s}, {jt:#04x}, {jf:#04x}, {k:#010x}),  /* #{i} */")
    print("/* }; */")
    print()
    print("/* struct sock_fprog prog = { .len = N, .filter = filter }; */")


def bp_label(code: int, k: int) -> str:
    """反解 BPF 指令为人读 macro。"""
    LDC = code & 0xFF
    if (code & 0x07) == 0x06:        # BPF_RET
        return f"LD_RET({k:#x})"
    if (code & 0x07) == 0x05:        # BPF_JMP
        return f"JUMP({k:#x})"
    if (code & 0x07) == 0x00:        # BPF_LD
        return f"LOAD({k})"
    return f"RAW({code:#x},{k:#x})"


def main():
    if len(sys.argv) < 2:
        print("usage:")
        print("  python3 seccomp_demo.py dump whitelist \"read,write,exit,exit_group\"")
        print("  python3 seccomp_demo.py arch-info")
        print("  python3 seccomp_demo.py actions-info")
        sys.exit(1)
    head = sys.argv[1]
    if head == "dump" and len(sys.argv) > 2 and sys.argv[2] == "whitelist":
        cmd_dump_whitelist(sys.argv[3:])
    elif head == "arch-info":
        dump_arch_info()
    elif head == "actions-info":
        dump_action_table()
    else:
        print("unknown:", head)
        sys.exit(1)


if __name__ == "__main__":
    main()
