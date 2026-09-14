/*
 * modrm_decoder.c —— x86-64 指令编码最小解码器
 * 演示 ModRM / SIB / REX 如何共同决定"第二操作数"的形态；覆盖 MOV(r/m,r 与
 * r,r/m) / ADD / SUB / CMP / CALL rel32 / RET / NOP / FF group5 / MOV r,imm32。
 * 编译：gcc -O2 -Wall -Wextra -pedantic modrm_decoder.c -o modrm_decoder
 * 参考：OSDev "X86-64 Instruction Encoding" + Intel SDM Vol.2 §2.1
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define REX_W 0x08u /* 64 位操作数 */
#define REX_R 0x04u /* 扩展 ModRM.reg */
#define REX_X 0x02u /* 扩展 SIB.index */
#define REX_B 0x01u /* 扩展 ModRM.rm / SIB.base */

typedef struct {
    uint8_t mod, reg, rm;
} ModRM;

typedef struct {
    uint8_t rex; /* 0 表示无 REX 前缀 */
    int     len; /* 本条指令总字节数 */
    char    text[128];
} Decoded;

static const char *const REG64[16] = {
    "rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi",
    "r8",  "r9",  "r10", "r11", "r12", "r13", "r14", "r15"
};
static const char *const REG32[16] = {
    "eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi",
    "r8d", "r9d", "r10d", "r11d", "r12d", "r13d", "r14d", "r15d"
};

static const char *reg_name(uint8_t width, int idx)
{
    return (width == 8 ? REG64 : REG32)[idx & 0xF];
}

/* 读取 n 字节小端整数（n ∈ {1,2,4,8}） */
static uint64_t read_le(const uint8_t *p, int n)
{
    uint64_t v = 0;
    int      i;
    for (i = 0; i < n; i++)
        v |= (uint64_t)p[i] << (8 * i);
    return v;
}

/* 把位移按 "[base + 0x..] / - 0x.." 形式写入 buf */
static void fmt_disp(char *buf, size_t n, int64_t d)
{
    if (d < 0)
        snprintf(buf, n, " - 0x%llx", (unsigned long long)(-d));
    else
        snprintf(buf, n, " + 0x%llx", (unsigned long long)d);
}

/*
 * 解码 "r/m 操作数"，返回消耗的额外字节数（SIB + disp）。
 * 分支顺序不可调换：mod==3 → SIB(rm==4) → RIP 相对(mod==0 && rm==5) → 普通内存。
 * 64 位模式下寻址寄存器恒为 64 位，与 REX.W 无关；只有 mod==3 才用 width。
 * 注意：RIP 相对分支在真实解码器中还要加上本条指令剩余长度。
 */
static int decode_rm(const uint8_t *p, uint8_t rex, ModRM m,
                     uint8_t width, char *out, size_t n)
{
    int consumed = 0;
    int ext_rm   = m.rm | ((rex & REX_B) ? 8 : 0);

    /* (1) mod=11：rm 是寄存器操作数，宽度受 REX.W / 66 控制 */
    if (m.mod == 3) {
        snprintf(out, n, "%s", reg_name(width, ext_rm));
        return 0;
    }

    /* (2) rm=100：后面跟一个 SIB 字节，基址/变址都在 SIB 里（均为 64 位寄存器） */
    if (m.rm == 4) {
        uint8_t sib       = p[consumed++];
        int     scale     = 1 << ((sib >> 6) & 3);
        int     index     = ((sib >> 3) & 7) | ((rex & REX_X) ? 8 : 0);
        int     has_index = !(index == 4 && !(rex & REX_X)); /* index=100(无 REX.X) = 无变址 */
        int     base      = (sib & 7) | ((rex & REX_B) ? 8 : 0);
        int     has_base  = !(base == 5 && m.mod == 0);      /* base=101 且 mod=00 = 无基址 */
        int     db        = (m.mod == 1) ? 1 : (m.mod == 2 ? 4 : 0);
        char    idx[32]   = "";
        char    bas[32]   = "";
        char    disp[48]  = "";

        if (!has_base) db = 4; /* 无基址时必然带 disp32（绝对地址） */
        if (has_index) snprintf(idx, sizeof idx, "%s*%d", REG64[index], scale);
        if (has_base)  snprintf(bas, sizeof bas, "%s", REG64[base]);
        if (db > 0) {
            int64_t d = (int64_t)read_le(p + consumed, db);
            d = (db == 1) ? (int8_t)d : (int32_t)d; /* disp8/disp32 都是有符号 */
            fmt_disp(disp, sizeof disp, d);
        }
        consumed += db;

        if (has_index && has_base)
            snprintf(out, n, "[%s + %s%s]", bas, idx, disp);
        else if (has_index)
            snprintf(out, n, "[%s%s]", idx, disp);   /* 绝对地址形式 [index*scale + disp32] */
        else if (has_base)
            snprintf(out, n, "[%s%s]", bas, disp);
        else
            snprintf(out, n, "[disp32%s]", disp);
        return consumed;
    }

    /* (3) mod=00 & rm=101：64 位模式特有的 RIP 相对寻址（相当于 mod=00 的绝对地址） */
    if (m.mod == 0 && m.rm == 5) {
        int32_t d = (int32_t)read_le(p, 4);
        snprintf(out, n, "[rip + 0x%x]", (unsigned)d);
        return 4;
    }

    /* (4) 普通 [base (+disp8/disp32)]：RIP 相对已被上面拦掉，base 恒为 64 位 */
    {
        int     db      = (m.mod == 1) ? 1 : (m.mod == 2 ? 4 : 0);
        char    disp[48] = "";
        if (db > 0) {
            int64_t d = (int64_t)read_le(p, db);
            d = (db == 1) ? (int8_t)d : (int32_t)d;
            fmt_disp(disp, sizeof disp, d);
        }
        snprintf(out, n, "[%s%s]", REG64[ext_rm], disp);
        return db;
    }
}

static ModRM parse_modrm(uint8_t b)
{
    ModRM m;
    m.mod = (uint8_t)((b >> 6) & 0x3);
    m.reg = (uint8_t)((b >> 3) & 0x7);
    m.rm  = (uint8_t)(b & 0x7);
    return m;
}

/*
 * 解码一条指令。返回 0 成功、-1 未知 opcode。
 * 只实现整数指令子集，足以演示字段交互。
 */
static int decode_insn(const uint8_t *code, Decoded *out)
{
    int     i;
    int     pos;
    uint8_t rex  = 0;
    uint8_t op;
    uint8_t width;

    memset(out, 0, sizeof *out);

    /* 阶段一：legacy prefix 扫描（最多 4 字节） */
    for (i = 0; i < 4; ) {
        uint8_t b = code[i];
        if (b == 0xF0 || b == 0xF2 || b == 0xF3) { i++; continue; }        /* group1 */
        if (b == 0x2E || b == 0x36 || b == 0x3E || b == 0x26 ||
            b == 0x64 || b == 0x65)              { i++; continue; }        /* group2 */
        if (b == 0x66 || b == 0x67)              { i++; continue; }        /* group3 / 4 */
        break;
    }

    /* 阶段二：REX（必须紧跟 opcode；真实解码器遇到新 legacy prefix 需清 0） */
    if (code[i] >= 0x40 && code[i] <= 0x4F) {
        rex = (uint8_t)(code[i] & 0x0F);
        i++;
    }
    out->rex = rex;

    op    = code[i++];
    width = (rex & REX_W) ? 8 : 4;
    pos   = i; /* pos 指向 opcode 之后的第一个字节 */

    switch (op) {
    case 0x90:
    case 0xC3:
        snprintf(out->text, sizeof out->text, op == 0x90 ? "nop" : "ret");
        out->len = pos;
        return 0;

    case 0xE8: {
        int32_t rel = (int32_t)read_le(code + pos, 4);
        snprintf(out->text, sizeof out->text, "call rel32=0x%x (下一条指令 + %d)",
                 (uint32_t)rel, rel);
        out->len = pos + 4;
        return 0;
    }

    case 0x89: /* MOV r/m, r —— reg 是源，rm 是目的 */
    case 0x8B: { /* MOV r, r/m —— reg 是目的，rm 是源 */
        ModRM m    = parse_modrm(code[pos]);
        int   ext  = m.reg | ((rex & REX_R) ? 8 : 0);
        char  rm[112];
        int   used = decode_rm(code + pos + 1, rex, m, width, rm, sizeof rm);
        if (op == 0x89)
            snprintf(out->text, sizeof out->text, "mov %s, %s", rm, reg_name(width, ext));
        else
            snprintf(out->text, sizeof out->text, "mov %s, %s", reg_name(width, ext), rm);
        out->len = pos + 1 + used;
        return 0;
    }

    case 0x01: /* ADD r/m, r */
    case 0x29: /* SUB r/m, r */
    case 0x39: { /* CMP r/m, r */
        const char *mn   = (op == 0x01) ? "add" : (op == 0x29) ? "sub" : "cmp";
        ModRM       m    = parse_modrm(code[pos]);
        int         ext  = m.reg | ((rex & REX_R) ? 8 : 0);
        char        rm[112];
        int         used = decode_rm(code + pos + 1, rex, m, width, rm, sizeof rm);
        snprintf(out->text, sizeof out->text, "%s %s, %s", mn, rm, reg_name(width, ext));
        out->len = pos + 1 + used;
        return 0;
    }

    case 0xFF: { /* group5：reg 字段作扩展操作码 */
        static const char *const G5[8] = {
            "inc", "dec", "call", "callf", "jmp", "jmpf", "push", NULL
        };
        ModRM m = parse_modrm(code[pos]);
        char  rm[112];
        int   used;
        if (G5[m.reg] == NULL)
            return -1;
        used = decode_rm(code + pos + 1, rex, m, width, rm, sizeof rm);
        snprintf(out->text, sizeof out->text, "%s %s", G5[m.reg], rm);
        out->len = pos + 1 + used;
        return 0;
    }

    default:
        if ((op & 0xF8) == 0xB8) { /* MOV r32/r64, imm32 */
            int      r = (op & 7) | ((rex & REX_B) ? 8 : 0);
            uint64_t v = read_le(code + pos, 4);
            snprintf(out->text, sizeof out->text, "mov %s, 0x%llx",
                     reg_name(width, r), (unsigned long long)v);
            out->len = pos + 4;
            return 0;
        }
        return -1;
    }
}

static void hexdump(const uint8_t *p, int n, char *out, size_t sz)
{
    size_t off = 0;
    int    i;
    for (i = 0; i < n && off + 4 < sz; i++)
        off += (size_t)snprintf(out + off, sz - off, "%02x ", p[i]);
}

typedef struct {
    const uint8_t *code;
    int            n;
    const char    *note;
} Case;

int main(void)
{
    static const uint8_t c0[] = { 0x48, 0x89, 0xD8 };
    static const uint8_t c1[] = { 0x48, 0x8B, 0x04, 0x25, 0x00, 0x10, 0x00, 0x00 };
    static const uint8_t c2[] = { 0x48, 0x8B, 0x44, 0x8B, 0x10 };
    static const uint8_t c3[] = { 0x48, 0x8B, 0x05, 0x10, 0x00, 0x00, 0x00 };
    static const uint8_t c4[] = { 0x4C, 0x89, 0xC0 };
    static const uint8_t c5[] = { 0x48, 0x8B, 0x04, 0xC5, 0x00, 0x20, 0x00, 0x00 };
    static const uint8_t c6[] = { 0x48, 0x01, 0xD8 };
    static const uint8_t c7[] = { 0xFF, 0x54, 0x24, 0x08 };
    static const uint8_t c8[] = { 0x62, 0xF1, 0x7C, 0x48, 0x28, 0xC0 }; /* 未支持 */
    static const uint8_t c9[] = { 0xC3 };

    static const Case cases[10] = {
        { c0, (int)sizeof c0, "寄存器-寄存器" },
        { c1, (int)sizeof c1, "SIB 无基址/无变址 + disp32" },
        { c2, (int)sizeof c2, "SIB 基址 + 变址*scale + disp8" },
        { c3, (int)sizeof c3, "RIP 相对寻址" },
        { c4, (int)sizeof c4, "REX.R 把 reg 扩到 r8" },
        { c5, (int)sizeof c5, "REX.X 把 index 扩到 r8" },
        { c6, (int)sizeof c6, "REX.W 切 64 位操作数" },
        { c7, (int)sizeof c7, "FF /2 + SIB(rsp) 基址" },
        { c8, (int)sizeof c8, "未实现 opcode（返回 -1）" },
        { c9, (int)sizeof c9, "单字节指令" }
    };

    int k;
    for (k = 0; k < 10; k++) {
        Decoded d;
        char    hex[96] = "";
        int     r;
        hexdump(cases[k].code, cases[k].n, hex, sizeof hex);
        r = decode_insn(cases[k].code, &d);
        printf("%-24s | %-28s | %s\n", hex, cases[k].note,
               r == 0 ? d.text : "<unknown opcode>");
        if (r == 0) printf("%-24s | rex=0x%x  len=%d byte(s)\n", "", d.rex, d.len);
    }
    return 0;
}
