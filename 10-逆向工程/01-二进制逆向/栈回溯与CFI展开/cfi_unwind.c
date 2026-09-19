/* CFI 展开的 C 侧最小实现：只做「解释 DW_CFA 程序 → 得到某一 pc 处的规则」这一层。
 *
 * 数据来源（本轮实测下载并提取正文）：
 *   binutils include/dwarf2.def —— DW_CFA_* 取值
 *     https://sourceware.org/git/?p=binutils-gdb.git;a=blob_plain;f=include/dwarf2.def
 *   System V AMD64 psABI Draft 0.99.6 §3.6.2 Figure 3.36 —— %rsp=7 / %rbp=6 / RA=16
 *     https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf
 *
 * 刻意不实现的部分：表达式型指令（DW_CFA_def_cfa_expression / expression /
 * val_expression）需要一个 DWARF 表达式求值器，涉及另一个子系统，越界就报错退出。
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define MAX_REG 17          /* 本 demo 只用到 0..16（RA=16） */

/* psABI Figure 3.36：%rsp=7, %rbp=6, Return Address=16 */
#define REG_RSP 7
#define REG_RBP 6
#define REG_RA  16

/* DW_CFA 操作码（binutils dwarf2.def） */
#define DW_CFA_advance_loc      0x40
#define DW_CFA_offset           0x80
#define DW_CFA_restore          0xc0
#define DW_CFA_nop              0x00
#define DW_CFA_set_loc          0x01
#define DW_CFA_advance_loc1     0x02
#define DW_CFA_advance_loc2     0x03
#define DW_CFA_advance_loc4     0x04
#define DW_CFA_offset_extended  0x05
#define DW_CFA_restore_extended 0x06
#define DW_CFA_undefined        0x07
#define DW_CFA_same_value       0x08
#define DW_CFA_register         0x09
#define DW_CFA_remember_state   0x0a
#define DW_CFA_restore_state    0x0b
#define DW_CFA_def_cfa          0x0c
#define DW_CFA_def_cfa_register 0x0d
#define DW_CFA_def_cfa_offset   0x0e
#define DW_CFA_def_cfa_sf       0x12
#define DW_CFA_def_cfa_offset_sf 0x13
#define DW_CFA_val_offset       0x14
#define DW_CFA_GNU_args_size    0x2e

/* 寄存器规则。kind 取值与 Python 侧一致。 */
enum rule_kind { RULE_UNSET, RULE_UNDEFINED, RULE_SAME, RULE_OFFSET,
                 RULE_REGISTER, RULE_VAL_OFFSET };

struct rule {
    enum rule_kind kind;
    long off;      /* RULE_OFFSET / RULE_VAL_OFFSET 用的偏移 */
    int reg;       /* RULE_REGISTER 用的寄存器号 */
};

struct cie {
    uint32_t code_align;
    long data_align;
    int ra_reg;
    const uint8_t *ins;
    size_t ins_len;
};

struct fde {
    uint64_t pc_begin;
    uint64_t pc_range;
    const uint8_t *ins;
    size_t ins_len;
};

struct row {
    int cfa_reg;
    long cfa_off;             /* CFA = reg[cfa_reg] + cfa_off */
    struct rule rules[MAX_REG];
    struct rule saved[MAX_REG];
    int has_saved;
    long args_size;
    uint64_t loc;
};

static uint64_t read_uleb(const uint8_t **p)
{
    uint64_t result = 0;
    int shift = 0;
    for (;;) {
        uint8_t b = *(*p)++;
        result |= (uint64_t)(b & 0x7f) << shift;
        if ((b & 0x80) == 0)
            return result;
        shift += 7;
    }
}

static long read_sleb(const uint8_t **p)
{
    uint64_t result = 0;
    int shift = 0;
    for (;;) {
        uint8_t b = *(*p)++;
        result |= (uint64_t)(b & 0x7f) << shift;
        shift += 7;
        if ((b & 0x80) == 0) {
            if (b & 0x40)
                return (long)(result - ((uint64_t)1 << shift));
            return (long)result;
        }
    }
}

/* 解释一条 CFI 指令。返回 0 表示成功；遇到未实现的操作码返回 -1。 */
static int apply_one(struct row *row, const uint8_t **p, const struct cie *cie,
                     const struct rule *initial)
{
    uint8_t op = *(*p)++;
    uint8_t primary = op & 0xc0;
    uint8_t arg = op & 0x3f;

    if (primary == DW_CFA_advance_loc) {
        row->loc += (uint64_t)arg * cie->code_align;
        return 0;
    }
    if (primary == DW_CFA_offset) {
        row->rules[arg].kind = RULE_OFFSET;
        row->rules[arg].off = (long)read_uleb(p) * cie->data_align;
        return 0;
    }
    if (primary == DW_CFA_restore) {
        row->rules[arg] = initial[arg];
        return 0;
    }

    switch (op) {
    case DW_CFA_nop:
        return 0;
    case DW_CFA_advance_loc1:
        row->loc += (uint64_t)*(*p)++ * cie->code_align;
        return 0;
    case DW_CFA_advance_loc2: {
        uint16_t d = (uint16_t)((*p)[0] | ((*p)[1] << 8));
        *p += 2;
        row->loc += (uint64_t)d * cie->code_align;
        return 0;
    }
    case DW_CFA_advance_loc4: {
        uint32_t d = (uint32_t)((*p)[0] | ((*p)[1] << 8) |
                               ((*p)[2] << 16) | ((uint32_t)(*p)[3] << 24));
        *p += 4;
        row->loc += (uint64_t)d * cie->code_align;
        return 0;
    }
    case DW_CFA_def_cfa:
        row->cfa_reg = (int)read_uleb(p);
        row->cfa_off = (long)read_uleb(p);   /* def_cfa 的偏移是**未被折算**的 */
        return 0;
    case DW_CFA_def_cfa_register:
        row->cfa_reg = (int)read_uleb(p);
        return 0;
    case DW_CFA_def_cfa_offset:
        row->cfa_off = (long)read_uleb(p);
        return 0;
    case DW_CFA_def_cfa_sf:
        row->cfa_reg = (int)read_uleb(p);
        row->cfa_off = read_sleb(p) * cie->data_align;
        return 0;
    case DW_CFA_def_cfa_offset_sf:
        row->cfa_off = read_sleb(p) * cie->data_align;
        return 0;
    case DW_CFA_offset_extended: {
        int reg = (int)read_uleb(p);
        row->rules[reg].kind = RULE_OFFSET;
        row->rules[reg].off = (long)read_uleb(p) * cie->data_align;
        return 0;
    }
    case DW_CFA_val_offset: {
        int reg = (int)read_uleb(p);
        row->rules[reg].kind = RULE_VAL_OFFSET;
        row->rules[reg].off = (long)read_uleb(p) * cie->data_align;
        return 0;
    }
    case DW_CFA_register: {
        int reg = (int)read_uleb(p);
        row->rules[reg].kind = RULE_REGISTER;
        row->rules[reg].reg = (int)read_uleb(p);
        return 0;
    }
    case DW_CFA_undefined: {
        int reg = (int)read_uleb(p);
        row->rules[reg].kind = RULE_UNDEFINED;
        return 0;
    }
    case DW_CFA_same_value: {
        int reg = (int)read_uleb(p);
        row->rules[reg].kind = RULE_SAME;
        return 0;
    }
    case DW_CFA_restore_extended: {
        int reg = (int)read_uleb(p);
        row->rules[reg] = initial[reg];
        return 0;
    }
    case DW_CFA_remember_state:
        memcpy(row->saved, row->rules, sizeof(row->rules));
        row->has_saved = 1;
        return 0;
    case DW_CFA_restore_state:
        if (!row->has_saved)
            return -1;
        memcpy(row->rules, row->saved, sizeof(row->rules));
        return 0;
    case DW_CFA_GNU_args_size:
        row->args_size = (long)read_uleb(p);
        return 0;
    default:
        return -1;   /* 表达式型与厂商扩展：显式失败，不静默跳过 */
    }
}

/* 跑到目标 pc 为止：先 CIE 的 initial instructions，再 FDE 自身指令。
 * 「会把 loc 推过 pc 的那条 advance」必须整条不生效，否则规则会提前一步。
 */
static int build_row(const struct cie *cie, const struct fde *fde, uint64_t pc,
                     struct row *out)
{
    struct rule initial[MAX_REG];
    const uint8_t *p;
    const uint8_t *end;

    memset(out, 0, sizeof(*out));
    out->cfa_reg = -1;

    p = cie->ins;
    end = cie->ins + cie->ins_len;
    while (p < end)
        if (apply_one(out, &p, cie, NULL) != 0)
            return -1;
    memcpy(initial, out->rules, sizeof(initial));

    out->loc = fde->pc_begin;
    p = fde->ins;
    end = fde->ins + fde->ins_len;
    while (p < end) {
        const uint8_t *save = p;
        uint8_t op = *save;
        uint8_t primary = op & 0xc0;
        struct row probe = *out;

        if (primary == DW_CFA_advance_loc || op == DW_CFA_advance_loc1 ||
            op == DW_CFA_advance_loc2 || op == DW_CFA_advance_loc4) {
            /* 先算推进量：越过 pc 就停，且**不把这条算进去** */
            const uint8_t *q = p;
            if (apply_one(&probe, &q, cie, initial) != 0)
                return -1;
            if (probe.loc > pc)
                break;
            *out = probe;
            p = q;
        } else if (apply_one(out, &p, cie, initial) != 0) {
            return -1;
        }
    }
    return 0;
}

int main(void)
{
    /* 与 Python 侧同一份合成数据：
     *   CIE: def_cfa %rsp, 8 ; offset RA, 1           (data_align = -8)
     *   FDE: advance 1 ; def_cfa_offset 16 ; offset %rbp 2 ; advance 3 ;
     *        def_cfa_register %rbp
     */
    static const uint8_t cie_ins[] = { DW_CFA_def_cfa, 7, 8,
                                       DW_CFA_offset | REG_RA, 1 };
    static const uint8_t fde_ins[] = {
        DW_CFA_advance_loc | 1, DW_CFA_def_cfa_offset, 16,
        DW_CFA_offset | REG_RBP, 2,
        DW_CFA_advance_loc | 3, DW_CFA_def_cfa_register, REG_RBP,
    };
    struct cie cie = { 1, -8, REG_RA, cie_ins, sizeof(cie_ins) };
    struct fde fde = { 0x400600, 0x40, fde_ins, sizeof(fde_ins) };
    struct row r;
    uint64_t pcs[] = { 0x400600, 0x400601, 0x400603, 0x400604 };
    size_t i;

    printf("reg%d=%s reg%d=%s RA=%d\n", REG_RSP, "%rsp", REG_RBP, "%rbp", REG_RA);
    for (i = 0; i < sizeof(pcs) / sizeof(pcs[0]); i++) {
        if (build_row(&cie, &fde, pcs[i], &r) != 0) {
            printf("pc=0x%llx -> 解析失败\n", (unsigned long long)pcs[i]);
            continue;
        }
        printf("pc=0x%llx  CFA = reg%d %+ld   RA kind=%d off=%ld   rbp kind=%d off=%ld\n",
               (unsigned long long)pcs[i], r.cfa_reg, r.cfa_off,
               r.rules[REG_RA].kind, r.rules[REG_RA].off,
               r.rules[REG_RBP].kind, r.rules[REG_RBP].off);
    }
    printf("RA 规则 type=%d(2=RULE_OFFSET), CFA-8 应为 %ld\n",
           r.rules[REG_RA].kind, r.rules[REG_RA].off);
    return 0;
}
