/*
 * bpf_kprobe.c — eBPF 挂载机制与指令编码(C 版,教学用)
 *
 * 两件事:
 *   1) 打印 kprobe 的 int3 打补丁四步生命周期(权威依据:kernel.org trace/kprobes.rst)
 *   2) 手写若干条真实 eBPF 指令并编码成 8 字节机器码,再解码回来校验
 *   3) 若以 root 且 tracefs 可用,则真的通过 kprobe_events 挂一个探针读 trace_pipe
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic bpf_kprobe.c -o bpf_kprobe
 * 运行: ./bpf_kprobe        (非 root 自动降级为"打印应执行的命令")
 */

#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* ------------------------------------------------------------------ 指令编码 */
/* 8 字节定长:opcode(8) | regs(8) | offset(16 signed) | imm(32 signed)
 * regs 字节在小端主机上是 | src_reg(高 4 bit) | dst_reg(低 4 bit) |
 * ALU/JMP 类:code 占 bit 4-7,source 占 bit 3
 * LD/ST   类:mode 占 bit 5-7,size 占 bit 3-4                                */
enum { LD = 0x0, LDX = 0x1, ST = 0x2, STX = 0x3, ALU = 0x4, JMP = 0x5, JMP32 = 0x6, ALU64 = 0x7 };

enum { BPF_ADD = 0x0, BPF_SUB = 0x1, BPF_MOV = 0xb };
enum { BPF_JA = 0x0, BPF_JNE = 0x5, BPF_CALL = 0x8, BPF_EXIT = 0x9 };
enum { BPF_K = 0, BPF_X = 1 };          /* 源操作数:立即数 / 寄存器 */
enum { BPF_MEM = 3, BPF_DW = 3 };       /* 访存 mode / size */
#define BPF_FP 10                       /* r10:只读帧指针 */
#define BPF_STACK_SIZE 512

/* 布局说明(未使用位域,避免实现相关的位域顺序):
 *   [0] opcode  [1] src<<4|dst  [2..3] offset  [4..7] imm
 * 真实的 <linux/bpf.h> 用:
 *   struct bpf_insn { __u8 code; __u8 dst_reg:4; __u8 src_reg:4;
 *                     __s16 off; __s32 imm; };                                  */

/* 手工拼 8 字节,避免位域布局差异:小端 = [opcode][src<<4|dst][off lo][off hi][imm x4] */
static void insn_encode(uint8_t out[8], uint8_t opcode, uint8_t dst, uint8_t src,
                        int16_t offset, int32_t imm)
{
    out[0] = opcode;
    out[1] = (uint8_t)((src << 4) | (dst & 0x0f));
    out[2] = (uint8_t)(offset & 0xff);
    out[3] = (uint8_t)((offset >> 8) & 0xff);
    out[4] = (uint8_t)(imm & 0xff);
    out[5] = (uint8_t)((imm >> 8) & 0xff);
    out[6] = (uint8_t)((imm >> 16) & 0xff);
    out[7] = (uint8_t)((imm >> 24) & 0xff);
}

static void insn_decode(const uint8_t raw[8], uint8_t *opcode, uint8_t *dst,
                        uint8_t *src, int16_t *offset, int32_t *imm)
{
    *opcode = raw[0];
    *dst = raw[1] & 0x0f;
    *src = raw[1] >> 4;
    *offset = (int16_t)((uint16_t)raw[2] | ((uint16_t)raw[3] << 8));
    *imm = (int32_t)((uint32_t)raw[4] | ((uint32_t)raw[5] << 8) |
                     ((uint32_t)raw[6] << 16) | ((uint32_t)raw[7] << 24));
}

static void hexdump(const uint8_t raw[8], char *buf, size_t n)
{
    size_t off = 0;
    for (int i = 0; i < 8; i++)
        off += (size_t)snprintf(buf + off, n - off, "%02x ", raw[i]);
}

/* ------------------------------------------------------------------ 主流程 */
static void explain_kprobe_lifecycle(void)
{
    puts("=== kprobe 的 int3 打补丁四步(kernel.org trace/kprobes.rst)===");
    puts("  ① 拷贝被探测的那条指令,把它的【首字节】替换为断点指令(int3 = 0xCC)");
    puts("  ② 命中时 CPU trap -> 保存寄存器 -> notifier_call_chain -> pre_handler");
    puts("  ③ 单步执行【别处保存的那份指令拷贝】");
    puts("     为什么不在原地单步:原地必须先撤掉断点,那会开一个");
    puts("     \"另一个 CPU 直接冲过探测点\"的时间窗,导致漏采");
    puts("  ④ 执行 post_handler(可选),再继续执行探测点之后的指令\n");
    puts("  kretprobe:入口探针命中时保存真实返回地址,把它换成 trampoline 地址;");
    puts("  函数返回时先跳 trampoline,handler 再把保存的返回地址写回。");
    puts("  并发实例由 kretprobe_instance 承载,maxactive<=0 时默认 max(10, 2*NR_CPUS),");
    puts("  实例不够只让 nmissed++(漏采,不是灾难)。");
    puts("  副作用:__builtin_return_address() 会看到 trampoline 地址。\n");
    puts("  黑名单:不能探测 kprobes 自身(递归 trap / double fault),");
    puts("          用 NOKPROBE_SYMBOL() 标记;x86-64 的 __switch_to() 直接 -EINVAL。\n");
}

static void demo_encoding(void)
{
    struct { const char *name; uint8_t op, dst, src; int16_t off; int32_t imm; uint8_t want; } cases[] = {
        { "BPF_MOV64_IMM   r0, 1",     (uint8_t)(ALU64 | (BPF_MOV << 4) | (BPF_K << 3)), 0, 0, 0, 1, 0xb7 },
        { "BPF_ADD_IMM     r8, 1",     (uint8_t)(ALU64 | (BPF_ADD << 4) | (BPF_K << 3)), 8, 0, 0, 1, 0x07 },
        { "BPF_SUB_IMM     r7, 1",     (uint8_t)(ALU64 | (BPF_SUB << 4) | (BPF_K << 3)), 7, 0, 0, 1, 0x17 },
        { "BPF_LDX_MEM(DW) r9, [r10-8]",(uint8_t)(LDX | (BPF_MEM << 5) | (BPF_DW << 3)), 9, BPF_FP, -8, 0, 0x79 },
        { "BPF_STX_MEM(DW) [r10-8], r8",(uint8_t)(STX | (BPF_MEM << 5) | (BPF_DW << 3)), BPF_FP, 8, -8, 0, 0x7b },
        { "BPF_JNE_IMM     r7, 0, -3", (uint8_t)(JMP | (BPF_JNE << 4) | (BPF_K << 3)), 7, 0, -3, 0, 0x55 },
        { "BPF_CALL        5 (ktime_ns)",(uint8_t)(JMP | (BPF_CALL << 4) | (BPF_K << 3)), 0, 0, 0, 5, 0x85 },
        { "BPF_EXIT",                  (uint8_t)(JMP | (BPF_EXIT << 4) | (BPF_K << 3)), 0, 0, 0, 0, 0x95 },
    };
    size_t n = sizeof(cases) / sizeof(cases[0]);
    int ok = 0;

    puts("=== eBPF 指令编码自检(8 字节定长)===");
    for (size_t i = 0; i < n; i++) {
        uint8_t raw[8], op, dst, src, hx[8];
        int16_t off;
        int32_t imm;
        char hex[32] = {0};

        insn_encode(raw, cases[i].op, cases[i].dst, cases[i].src, cases[i].off, cases[i].imm);
        hexdump(raw, hex, sizeof(hex));
        insn_decode(raw, &op, &dst, &src, &off, &imm);
        insn_encode(hx, op, dst, src, off, imm);

        int roundtrip = memcmp(raw, hx, 8) == 0;
        int opcode_ok = (raw[0] == cases[i].want);
        ok += (roundtrip && opcode_ok);
        printf("  %-30s opcode=0x%02x(期望 0x%02x%s)  dst=r%u src=r%u off=%d imm=%d\n"
               "  %-30s bytes: %s 编解码往返一致=%s\n",
               cases[i].name, raw[0], cases[i].want, opcode_ok ? " OK" : " NG",
               dst, src, off, imm, "", hex, roundtrip ? "yes" : "NO");
    }
    printf("  -> %d/%zu 条与真实 BPF 助手宏常量一致,编解码往返全部无损\n\n", ok, n);
}

/* 通过 tracefs kprobe_events 挂探针(需要 root) */
static int attach_kprobe_via_tracefs(void)
{
    static const char *candidates[] = {
        "/sys/kernel/tracing/kprobe_events",
        "/sys/kernel/debug/tracing/kprobe_events",
    };
    const char *path = NULL;
    FILE *f;

    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        if (access(candidates[i], W_OK) == 0) { path = candidates[i]; break; }
    }
    if (!path) {
        puts("=== 真实挂载(非 root 或 tracefs 不可写 -> 降级为命令清单)===");
        puts("  sudo mount -t tracefs nodev /sys/kernel/tracing");
        puts("  # 挂一个 kprobe 到 do_sys_open 入口:");
        puts("  sudo sh -c 'echo \"p:devinsn_open do_sys_open\" > /sys/kernel/tracing/kprobe_events'");
        puts("  sudo sh -c 'echo 1 > /sys/kernel/tracing/events/kprobes/devinsn_open/enable'");
        puts("  sudo cat /sys/kernel/tracing/trace_pipe | head -20");
        puts("  sudo sh -c 'echo \"-:devinsn_open\" > /sys/kernel/tracing/kprobe_events'   # 清理");
        puts("  (每次 open(2) 都会命中;bpftrace 的 \"kprobe:do_sys_open\" 走的就是这条路径)\n");
        return 0;
    }

    puts("=== 真实挂载 kprobe(通过 tracefs kprobe_events)===");
    f = fopen(path, "a");
    if (!f) { fprintf(stderr, "open %s: %s\n", path, strerror(errno)); return -1; }
    if (fputs("p:devinsn_open do_sys_open\n", f) == EOF) {
        fprintf(stderr, "write: %s\n", strerror(errno));
        fclose(f);
        return -1;
    }
    fclose(f);
    printf("  已注册探针 p:devinsn_open(内核此时把 do_sys_open 首字节改成了 0xCC)\n");
    puts("  清理命令: echo '-:devinsn_open' > /sys/kernel/tracing/kprobe_events");
    return 0;
}

int main(void)
{
    printf("eBPF 虚拟机常量:寄存器 11 个(r0-r10),r10 是只读帧指针,栈固定 %d 字节\n\n",
           BPF_STACK_SIZE);
    explain_kprobe_lifecycle();
    demo_encoding();
    attach_kprobe_via_tracefs();
    puts("提示:eBPF 只是执行引擎,钩子由 kprobe/uprobe/tracepoint/fentry 提供;");
    puts("      验证器保证\"程序本身安全\",它【不是】审计\"程序在干什么\"的安全工具。");
    return 0;
}
