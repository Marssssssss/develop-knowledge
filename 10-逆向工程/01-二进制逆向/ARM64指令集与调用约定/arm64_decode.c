/* arm64_decode.c — A64 定长指令解码器(与 arm64_decode.py 同题的 C 实现)
 *
 * 依据: Arm《A64 Instruction Set Architecture Guide》102374 1.3 与 A-profile A64 ISA
 *       「Index by Encoding」位域表(ddi0602);AAPCS64 规范(ARM-software/abi-aa)。
 *
 * 覆盖: 分支(寄存器/立即/条件/比较)、PC 相对、加/减(立即数/移位寄存器)、
 *       逻辑移位寄存器、宽立即数搬移、载入/存储(无符号偏移/imm9/pair)、NOP。
 * 刻意保留「别名折叠」(mov x29,sp / ret / mov x0,x1),与 objdump 显示习惯一致。
 *
 * 构建: cc -std=c99 -Wall -Wextra -O2 -o arm64_decode arm64_decode.c
 * 运行: ./arm64_decode       退出码 0 表示 8 条 golden 指令全部命中预期文本
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static unsigned bits(uint32_t w, int hi, int lo) {
    return (w >> lo) & ((1u << (hi - lo + 1)) - 1);
}

static int32_t sx(uint32_t v, int n) {
    return (v >> (n - 1)) ? (int32_t)v - (1 << n) : (int32_t)v;
}

static const char *COND[16] = {
    "eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
    "hi", "ls", "ge", "lt", "gt", "le", "al", "nv"
};

static const char *SHIFT[4] = { "lsl", "lsr", "asr", "ror" };

static void reg(char *out, size_t n, unsigned i, int is64) {
    if (is64)
        snprintf(out, n, i == 31 ? "xzr" : "x%u", i);
    else
        snprintf(out, n, i == 31 ? "wzr" : "w%u", i);
}

static void spr(char *out, size_t n, unsigned i) {
    snprintf(out, n, i == 31 ? "sp" : "x%u", i);
}

/* 解码一条指令到 out(长度 n)。返回 1 = 已识别,0 = 未覆盖。 */
static int a64_decode(uint32_t w, uint64_t pc, char *out, size_t n) {
    char a[32], b[32];
    /* 分支(寄存器): 1101011 opc(24:21) 11111 000000 Rn 00000 */
    if ((w >> 25) == 0x6Bu && bits(w, 20, 16) == 0x1F &&
        bits(w, 15, 10) == 0 && bits(w, 4, 0) == 0) {
        unsigned opc = bits(w, 24, 21), rn = bits(w, 9, 5);
        const char *nm = opc == 0 ? "br" : opc == 1 ? "blr" :
                         opc == 2 ? "ret" : opc == 4 ? "eret" : NULL;
        if (!nm) return 0;
        if (opc == 4) { snprintf(out, n, "eret"); return 1; }
        if (opc == 2 && rn == 30) { snprintf(out, n, "ret"); return 1; }
        snprintf(out, n, "%s x%u", nm, rn);
        return 1;
    }
    /* 条件分支: 0101010 0 imm19(23:5) 0 cond(3:0) */
    if ((w & 0xFF000010u) == 0x54000000u) {
        int32_t off = sx(bits(w, 23, 5), 19) << 2;
        snprintf(out, n, "b.%s 0x%llx", COND[bits(w, 3, 0)],
                 (unsigned long long)(pc + off));
        return 1;
    }
    /* B / BL: 000101 / 100101 + imm26 */
    if ((w >> 26) == 0x05u || (w >> 26) == 0x25u) {
        int32_t off = sx(bits(w, 25, 0), 26) << 2;
        snprintf(out, n, "%s 0x%llx", bits(w, 31, 31) ? "bl" : "b",
                 (unsigned long long)(pc + off));
        return 1;
    }
    /* ADR / ADRP: op immlo(30:29) 10000 immhi(23:5) Rd */
    if ((w & 0x1F000000u) == 0x10000000u) {
        int32_t imm = sx((bits(w, 23, 5) << 2) | bits(w, 30, 29), 21);
        if (bits(w, 31, 31))
            snprintf(out, n, "adrp x%u, #0x%x", bits(w, 4, 0), (unsigned)(imm << 12));
        else
            snprintf(out, n, "adr x%u, #%d", bits(w, 4, 0), imm);
        return 1;
    }
    /* 加/减(立即数): sf op S 10001 sh(23:22) imm12(21:10) Rn Rd */
    if ((w & 0x1F000000u) == 0x11000000u) {
        unsigned sf = bits(w, 31, 31), op = bits(w, 30, 30), s = bits(w, 29, 29);
        unsigned sh = bits(w, 23, 22), imm12 = bits(w, 21, 10);
        unsigned rn = bits(w, 9, 5), rd = bits(w, 4, 0);
        const char *base = op ? (s ? "subs" : "sub") : (s ? "adds" : "add");
        if (!op && imm12 == 0 && sh == 0 && rn == 31 && rd != 31) {
            reg(a, sizeof a, rd, sf);
            snprintf(out, n, "mov %s, sp", a);
            return 1;
        }
        reg(a, sizeof a, rd, sf);
        spr(b, sizeof b, rn);
        snprintf(out, n, "%s %s, %s, #0x%x", base, a, b, imm12 << (sh ? 12 : 0));
        return 1;
    }
    /* 加/减(移位寄存器): sf op S 01011 shift 0 Rm imm6 Rn Rd */
    if ((w & 0x1F200000u) == 0x0B000000u) {
        unsigned sf = bits(w, 31, 31), op = bits(w, 30, 30), s = bits(w, 29, 29);
        unsigned sh = bits(w, 23, 22), rm = bits(w, 20, 16), imm6 = bits(w, 15, 10);
        unsigned rn = bits(w, 9, 5), rd = bits(w, 4, 0);
        const char *base = op ? (s ? "subs" : "sub") : (s ? "adds" : "add");
        char c[32];
        reg(a, sizeof a, rd, sf); reg(b, sizeof b, rn, sf);
        reg(c, sizeof c, rm, sf);
        if (imm6)
            snprintf(out, n, "%s %s, %s, %s, %s #%u", base, a, b, c, SHIFT[sh], imm6);
        else
            snprintf(out, n, "%s %s, %s, %s", base, a, b, c);
        return 1;
    }
    /* 逻辑(移位寄存器): sf opc(30:29) 01010 shift N(21) Rm imm6 Rn Rd */
    if ((w & 0x1F000000u) == 0x0A000000u) {
        unsigned sf = bits(w, 31, 31), opc = bits(w, 30, 29), nn = bits(w, 21, 21);
        unsigned sh = bits(w, 23, 22), rm = bits(w, 20, 16), imm6 = bits(w, 15, 10);
        unsigned rn = bits(w, 9, 5), rd = bits(w, 4, 0);
        const char *nm = opc == 0 ? (nn ? "bic" : "and") :
                         opc == 1 ? (nn ? "orn" : "orr") :
                         opc == 2 ? (nn ? "eon" : "eor") : (nn ? "bics" : "ands");
        char c[32];
        reg(a, sizeof a, rd, sf); reg(b, sizeof b, rn, sf);
        reg(c, sizeof c, rm, sf);
        if (!nn && opc == 1 && rn == 31 && imm6 == 0 && sh == 0) {
            snprintf(out, n, "mov %s, %s", a, c);
            return 1;
        }
        if (imm6)
            snprintf(out, n, "%s %s, %s, %s, %s #%u", nm, a, b, c, SHIFT[sh], imm6);
        else
            snprintf(out, n, "%s %s, %s, %s", nm, a, b, c);
        return 1;
    }
    /* 宽立即数搬移: sf opc(30:29) 100101 hw(22:21) imm16(20:5) Rd */
    if ((w & 0x1F800000u) == 0x12800000u) {
        unsigned sf = bits(w, 31, 31), opc = bits(w, 30, 29);
        unsigned hw = bits(w, 22, 21), imm16 = bits(w, 20, 5), rd = bits(w, 4, 0);
        const char *nm = opc == 0 ? "movn" : opc == 2 ? "movz" : opc == 3 ? "movk" : NULL;
        if (!nm) return 0;
        reg(a, sizeof a, rd, sf);
        if (opc == 2 && hw == 0)
            snprintf(out, n, "mov %s, #0x%x", a, imm16);
        else if (hw)
            snprintf(out, n, "%s %s, #0x%x, lsl #%u", nm, a, imm16, hw * 16);
        else
            snprintf(out, n, "%s %s, #0x%x", nm, a, imm16);
        return 1;
    }
    /* 载入/存储(无符号偏移): size 111 V 01 opc imm12 Rn Rt */
    if ((w & 0x3B000000u) == 0x39000000u) {
        unsigned v = bits(w, 26, 26), opc = bits(w, 23, 22);
        unsigned scale = bits(w, 31, 30), imm12 = bits(w, 21, 10);
        unsigned rn = bits(w, 9, 5), rt = bits(w, 4, 0);
        const char *nm = (v || opc > 1) ? NULL : (opc == 0 ? "str" : "ldr");
        if (!nm) return 0;
        reg(a, sizeof a, rt, 1); spr(b, sizeof b, rn);
        snprintf(out, n, "%s %s, [%s, #0x%x]", nm, a, b, imm12 << scale);
        return 1;
    }
    /* 载入/存储(imm9): size 111 V 00 opc imm9 idx Rn Rt */
    if ((w & 0x3B200000u) == 0x38000000u) {
        unsigned v = bits(w, 26, 26), opc = bits(w, 23, 22), idx = bits(w, 11, 10);
        int32_t off = sx(bits(w, 20, 12), 9);
        unsigned rn = bits(w, 9, 5), rt = bits(w, 4, 0);
        const char *nm = v ? NULL : (opc == 0 ? "str" : opc == 1 ? "ldr" : NULL);
        if (!nm) return 0;
        reg(a, sizeof a, rt, 1); spr(b, sizeof b, rn);
        if (idx == 1)
            snprintf(out, n, "%s %s, [%s], #%d", nm, a, b, off);
        else
            snprintf(out, n, "%s %s, [%s, #%d]%s", nm, a, b, off, idx == 3 ? "!" : "");
        return 1;
    }
    /* 载入/存储 pair: opc=10 101 V 0 idx(24:23) L imm7 Rt2 Rn Rt */
    if ((w & 0x3A000000u) == 0x28000000u && bits(w, 31, 30) == 2) {
        unsigned v = bits(w, 26, 26), idx = bits(w, 24, 23), l = bits(w, 22, 22);
        int32_t off = sx(bits(w, 21, 15), 7) << (v ? 4 : 3);
        unsigned rt2 = bits(w, 14, 10), rn = bits(w, 9, 5), rt = bits(w, 4, 0);
        spr(b, sizeof b, rn);
        if (idx == 1)
            snprintf(out, n, "%s x%u, x%u, [%s], #%d", l ? "ldp" : "stp", rt, rt2, b, off);
        else
            snprintf(out, n, "%s x%u, x%u, [%s, #%d]%s", l ? "ldp" : "stp", rt, rt2, b,
                     off, idx == 3 ? "!" : "");
        return 1;
    }
    if (w == 0xD503201Fu) { snprintf(out, n, "nop"); return 1; }
    return 0;
}

/* ---- 自检: 真实 GCC 4.8.1 ARM64 清单 ---------------------------------- */
struct golden { uint64_t pc; uint32_t word; const char *want; };

static const struct golden G[] = {
    { 0x400590, 0xA9BF7BFDu, "stp x29, x30, [sp, #-16]!" },
    { 0x400594, 0x910003FDu, "mov x29, sp" },
    { 0x400598, 0x90000000u, "adrp x0, #0x0" },
    { 0x40059C, 0x91192000u, "add x0, x0, #0x648" },
    { 0x4005A0, 0x97FFFFA0u, "bl 0x400420" },
    { 0x4005A4, 0x52800000u, "mov w0, #0x0" },
    { 0x4005A8, 0xA8C17BFDu, "ldp x29, x30, [sp], #16" },
    { 0x4005AC, 0xD65F03C0u, "ret" },
};

int main(void) {
    int fails = 0;
    for (size_t i = 0; i < sizeof G / sizeof G[0]; i++) {
        char got[64];
        int ok = a64_decode(G[i].word, G[i].pc, got, sizeof got);
        int hit = ok && strcmp(got, G[i].want) == 0;
        if (!hit) fails++;
        printf("  [%s] 0x%08X @0x%llX -> %s\n", hit ? "PASS" : "FAIL",
               G[i].word, (unsigned long long)G[i].pc, ok ? got : "??");
    }
    /* 位域断言: 一位之差就是另一条指令 */
    {
        char a[64], b[64], c[64];
        int ok = 1;
        ok &= a64_decode(0x8B010000u, 0, a, sizeof a) && !strcmp(a, "add x0, x0, x1");
        ok &= a64_decode(0xCB010000u, 0, b, sizeof b) && !strcmp(b, "sub x0, x0, x1");
        ok &= a64_decode(0xEB010000u, 0, c, sizeof c) && !strcmp(c, "subs x0, x0, x1");
        if (!ok) fails++;
        printf("  [%s] bit30/29 翻转: %s | %s | %s\n", ok ? "PASS" : "FAIL", a, b, c);
    }
    /* 位域常量断言 */
    {
        int ok = bits(0x91192000u, 28, 24) == 0x11u &&
                 bits(0x91192000u, 21, 10) == 0x648u &&
                 bits(0xCB010000u, 28, 24) == 0x0Bu &&
                 bits(0x52800000u, 28, 23) == 0x25u;
        if (!ok) fails++;
        printf("  [%s] 组标识位 [28:24]=10001/01011、[28:23]=100101\n", ok ? "PASS" : "FAIL");
    }
    printf("\n%s (fails=%d)\n", fails ? "SOME CHECKS FAILED" : "ALL CHECKS PASSED", fails);
    return fails ? 1 : 0;
}
