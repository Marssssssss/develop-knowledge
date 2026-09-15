/* cgroup eBPF 程序的指令编码与 ELF section 解析(C 实现)。
 *
 * 来源:
 *   https://docs.kernel.org/bpf/libbpf/program_types.html
 *   https://docs.kernel.org/6.18/userspace-api/ebpf/syscall.html
 *
 * 本文件实现三件事:
 *   1. 还原 UAPI 里的 struct bpf_insn 布局(含 dst_reg/src_reg 两个 4 位域)
 *   2. 汇编一段最小 cgroup 程序:LD_MAP_FD -> MOV -> CALL -> EXIT,
 *      并给出 8 字节小端编码(这正是 BPF_PROG_LOAD 收进去的字节流)
 *   3. libbpf 的 SEC("...") 名 -> (程序类型, attach 类型) 解析与校验
 *
 * 编译:gcc -O2 -Wall -Wextra -pedantic -std=c11 bpf_insn.c -o bpf_insn
 * 注意:本机无 C 工具链,本文件走人工代码审查 + 括号配平程序化校验。
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* ---- 与 <linux/bpf.h> 对齐的常量 ---- */
#define BPF_LD              0x00u
#define BPF_ALU64           0x07u
#define BPF_JMP             0x05u
#define BPF_DW              0x18u
#define BPF_IMM             0x00u
#define BPF_MOV             0xb0u
#define BPF_CALL            0x80u
#define BPF_EXIT            0x90u
#define BPF_PSEUDO_MAP_FD   1u      /* src_reg 里表示"imm 是 map fd" */
/* helper ID 按 UAPI <linux/bpf.h> 的 __BPF_FUNC_MAPPER 枚举顺序取值。
 * 本 demo 只用它作为 CALL 的立即数来演示编码,不依赖该 helper 的具体行为。 */
#define BPF_FUNC_get_current_pid_tgid 14u

#define BPF_REG_0           0u
#define BPF_REG_1           1u
#define BPF_REG_2           2u

/* struct bpf_insn {
 *     __u8  code;         opcode
 *     __u8  dst_reg:4;    目标寄存器(低半字节)
 *     __u8  src_reg:4;    源寄存器(高半字节)
 *     __s16 off;          有符号偏移
 *     __s32 imm;          有符号立即数
 * };
 * 共 8 字节,且**必须**是 8 字节 —— 第二条指令的 imm 承载 64 位立即数的高 32 位。
 */
typedef struct {
    uint8_t  code;
    uint8_t  regs;   /* 低 4 位 dst_reg,高 4 位 src_reg */
    int16_t  off;
    int32_t  imm;
} bpf_insn_t;

static bpf_insn_t insn(uint8_t code, uint8_t dst, uint8_t src,
                       int16_t off, int32_t imm)
{
    bpf_insn_t i;
    i.code = code;
    i.regs = (uint8_t)((dst & 0x0fu) | ((src & 0x0fu) << 4));
    i.off  = off;
    i.imm  = imm;
    return i;
}

static uint8_t insn_dst(const bpf_insn_t *i) { return (uint8_t)(i->regs & 0x0fu); }
static uint8_t insn_src(const bpf_insn_t *i) { return (uint8_t)(i->regs >> 4); }

/* 把一条指令按小端编码成 8 字节。 */
static void encode(const bpf_insn_t *i, uint8_t out[8])
{
    uint16_t off = (uint16_t)i->off;
    uint32_t imm = (uint32_t)i->imm;
    out[0] = i->code;
    out[1] = i->regs;
    out[2] = (uint8_t)(off & 0xffu);
    out[3] = (uint8_t)((off >> 8) & 0xffu);
    out[4] = (uint8_t)(imm & 0xffu);
    out[5] = (uint8_t)((imm >> 8) & 0xffu);
    out[6] = (uint8_t)((imm >> 16) & 0xffu);
    out[7] = (uint8_t)((imm >> 24) & 0xffu);
}

static void decode(const uint8_t in[8], bpf_insn_t *i)
{
    uint16_t off = (uint16_t)(in[2] | ((uint16_t)in[3] << 8));
    uint32_t imm = (uint32_t)in[4] | ((uint32_t)in[5] << 8) |
                   ((uint32_t)in[6] << 16) | ((uint32_t)in[7] << 24);
    i->code = in[0];
    i->regs = in[1];
    i->off  = (int16_t)off;
    i->imm  = (int32_t)imm;
}

static void hexdump(const uint8_t *p, size_t n)
{
    size_t k;
    for (k = 0; k < n; k++) {
        printf("%02x", p[k]);
        if ((k + 1u) % 8u == 0u) {
            printf(" ");
        }
    }
}

/* ---- SEC() 名 -> (程序类型, attach 类型) ---- */
typedef struct {
    const char *section;
    const char *prog_type;
    const char *attach_type;
} SectionEntry;

static const SectionEntry SECTION_TABLE[] = {
    /* ELF section 名        程序类型                             attach 类型 */
    {"cgroup/dev",          "BPF_PROG_TYPE_CGROUP_DEVICE",    "BPF_CGROUP_DEVICE"},
    {"cgroup/skb",          "BPF_PROG_TYPE_CGROUP_SKB",       "BPF_CGROUP_INET_INGRESS"},
    {"cgroup_skb/ingress",  "BPF_PROG_TYPE_CGROUP_SKB",       "BPF_CGROUP_INET_INGRESS"},
    {"cgroup_skb/egress",   "BPF_PROG_TYPE_CGROUP_SKB",       "BPF_CGROUP_INET_EGRESS"},
    {"cgroup/connect4",     "BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_INET4_CONNECT"},
    {"cgroup/sendmsg4",     "BPF_PROG_TYPE_CGROUP_SOCK_ADDR", "BPF_CGROUP_UDP4_SENDMSG"},
    {"cgroup/post_bind4",   "BPF_PROG_TYPE_CGROUP_SOCK",      "BPF_CGROUP_INET4_POST_BIND"},
    {"cgroup/sock_create",  "BPF_PROG_TYPE_CGROUP_SOCK",      "BPF_CGROUP_INET_SOCK_CREATE"},
    {"cgroup/sysctl",       "BPF_PROG_TYPE_CGROUP_SYSCTL",    "BPF_CGROUP_SYSCTL"},
    {"sockops",             "BPF_PROG_TYPE_SOCK_OPS",         "BPF_CGROUP_SOCK_OPS"},
};

static const SectionEntry *resolve_section(const char *name, const char **err)
{
    size_t k;
    /* 非 cgroup 类程序不能附加到 cgroup */
    if (strcmp(name, "xdp") == 0 || strcmp(name, "tc") == 0 ||
        strcmp(name, "classifier") == 0 || strcmp(name, "socket") == 0 ||
        strcmp(name, "kprobe") == 0) {
        *err = "不是 cgroup 类程序,不能附加到 cgroup";
        return NULL;
    }
    for (k = 0; k < sizeof(SECTION_TABLE) / sizeof(SECTION_TABLE[0]); k++) {
        if (strcmp(name, SECTION_TABLE[k].section) == 0) {
            *err = NULL;
            return &SECTION_TABLE[k];
        }
    }
    *err = "未知的 section 名";
    return NULL;
}

int main(void)
{
    /* 最小 cgroup/skb 程序:
     *   r1 = map_fd          (LD_MAP_FD,占 2 条指令)
     *   r2 = r1              (MOV64)
     *   call 14              (get_current_pid_tgid)
     *   exit
     */
    bpf_insn_t prog[5];
    uint8_t    bytes[5 * 8];
    size_t     k, n;
    const char *err = NULL;
    const SectionEntry *e;

    printf("== bpf_insn 编码与 SEC() 解析(C) ==\n");
    printf("sizeof(bpf_insn_t) = %u(必须是 8,否则 64 位立即数装不下)\n",
           (unsigned)sizeof(bpf_insn_t));
    printf("sizeof(prog) = %u\n", (unsigned)sizeof(prog));

    prog[0] = insn((uint8_t)(BPF_LD | BPF_DW | BPF_IMM), BPF_REG_1, BPF_PSEUDO_MAP_FD, 0, 7);
    prog[1] = insn(0, 0, 0, 0, 0);                       /* 宽指令的第二条:全 0 */
    prog[2] = insn((uint8_t)(BPF_ALU64 | BPF_MOV), BPF_REG_2, BPF_REG_1, 0, 0);
    prog[3] = insn((uint8_t)(BPF_JMP | BPF_CALL), 0, 0, 0, BPF_FUNC_get_current_pid_tgid);
    prog[4] = insn((uint8_t)(BPF_JMP | BPF_EXIT), 0, 0, 0, 0);

    printf("指令逐条还原:\n");
    for (k = 0; k < 5u; k++) {
        printf("  [%zu] code=0x%02x dst=r%u src=%u off=%d imm=%d\n", k,
               prog[k].code, insn_dst(&prog[k]), insn_src(&prog[k]),
               prog[k].off, prog[k].imm);
    }

    n = 0;
    for (k = 0; k < 5u; k++) {
        encode(&prog[k], &bytes[n]);
        n += 8u;
    }
    printf("小端字节流(%zu 字节):", n);
    hexdump(bytes, n);
    printf("\n");

    /* 往返一致性:解码回来必须与原始指令完全一致 */
    {
        int same = 1;
        for (k = 0; k < 5u; k++) {
            bpf_insn_t back;
            decode(&bytes[k * 8u], &back);
            if (back.code != prog[k].code || back.regs != prog[k].regs ||
                back.off != prog[k].off || back.imm != prog[k].imm) {
                same = 0;
            }
        }
        printf("编码->解码往返一致: %s\n", same ? "yes" : "no");
    }

    /* LD_MAP_FD 的 src_reg 必须是 BPF_PSEUDO_MAP_FD,否则会被当作普通立即数 */
    printf("LD_MAP_FD 的 src_reg = %u(应为 %u=BPF_PSEUDO_MAP_FD)\n",
           insn_src(&prog[0]), BPF_PSEUDO_MAP_FD);
    printf("宽指令第二条必须为全 0: code=%u imm=%d\n", prog[1].code, prog[1].imm);

    printf("\nSEC() 解析:\n");
    e = resolve_section("cgroup_skb/ingress", &err);
    printf("  cgroup_skb/ingress -> %s / %s\n",
           e ? e->prog_type : err, e ? e->attach_type : "");
    e = resolve_section("cgroup/connect4", &err);
    printf("  cgroup/connect4    -> %s / %s\n",
           e ? e->prog_type : err, e ? e->attach_type : "");
    e = resolve_section("sockops", &err);
    printf("  sockops            -> %s / %s\n",
           e ? e->prog_type : err, e ? e->attach_type : "");
    e = resolve_section("xdp", &err);
    printf("  xdp                -> 拒绝: %s\n", err);
    e = resolve_section("cgroup/nope", &err);
    printf("  cgroup/nope        -> 拒绝: %s\n", err);
    (void)e;

    return 0;
}
