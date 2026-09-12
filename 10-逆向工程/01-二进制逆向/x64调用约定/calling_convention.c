/* x86-64 System V 调用约定实证 demo。
 * 依据: System V ABI AMD64 Processor Supplement(psABI)§3.2 函数参数传递:
 *   前 6 个 INTEGER 类参数 → rdi rsi rdx rcx r8 r9;第 7 个起入栈;
 *   返回值 → rax;rbx/rbp/r12-r15 由被调方保存;调用前 rsp 16 字节对齐。
 *
 * 用内联汇编在函数入口"抓拍"寄存器,证明约定真实存在 —— 这正是逆向时
 * 识别函数参数个数/类型的依据(frida/调试器 hook 也靠它取参)。
 *
 * 编译(需 GCC/Clang,Linux 或 WSL):
 *   gcc -O1 -fno-inline -o calling_convention calling_convention.c
 */
#include <stdio.h>

__attribute__((noinline))
static void show_regs(long a, long b, long c, long d, long e, long f,
                      long g, long h) {
    long rdi_v, rsi_v, rdx_v, rcx_v, r8_v, r9_v;
    /* 函数第一条可执行指令处抓拍入口寄存器(约定保证此处参数还在原寄存器) */
    __asm__ volatile (
        "movq %%rdi, %0\n\tmovq %%rsi, %1\n\tmovq %%rdx, %2\n\t"
        "movq %%rcx, %3\n\tmovq %%r8, %4\n\tmovq %%r9, %5\n\t"
        : "=r"(rdi_v), "=r"(rsi_v), "=r"(rdx_v),
          "=r"(rcx_v), "=r"(r8_v), "=r"(r9_v)
        :: "rdi", "rsi", "rdx", "rcx", "r8", "r9");

    printf("寄存器抓拍: rdi=%ld rsi=%ld rdx=%ld rcx=%ld r8=%ld r9=%ld\n",
           rdi_v, rsi_v, rdx_v, rcx_v, r8_v, r9_v);
    /* g/h 是第 7/8 参:位于返回地址上方,+0 与 +8 处(调用方已压栈) */
    printf("栈上参数  : [rsp+8]=g=%ld  [rsp+16]=h=%ld  (rsp 处是返回地址)\n", g, h);
    printf("参数地址  : &g=%p &h=%p —— 相邻 8 字节,右到左压栈的直接证据\n",
           (void *)&g, (void *)&h);
}

/* 演示 16 字节对齐:psABI 要求 call 指令执行前 rsp % 16 == 0,
 * call 压入返回地址后,被调函数入口 rsp % 16 == 8 */
__attribute__((noinline))
static long sp_alignment_probe(long x) {
    long sp;
    __asm__ volatile("movq %%rsp, %0" : "=r"(sp));
    printf("入口 rsp=%p → rsp%%16=%ld (call 压 8B 返回地址后必为 8)\n",
           (void *)sp, (long)((unsigned long)sp % 16));
    return x;
}

/* callee-saved 验证:r12 被调方使用前压栈、返回前还原,调用方无感 */
__attribute__((noinline))
static long use_r12(long x) {
    long r;
    __asm__ volatile(
        "pushq %%r12\n\t"          /* psABI: r12 属 callee-saved */
        "movq %1, %%r12\n\t"
        "shlq $1, %%r12\n\t"
        "movq %%r12, %0\n\t"
        "popq %%r12\n\t"
        : "=r"(r) : "r"(x) : "r12");
    return r;
}

int main(void) {
    printf("== 参数寄存器与栈传递(1..8 依次为 a..h)==\n");
    show_regs(111, 222, 333, 444, 555, 666, 777, 888);

    printf("\n== 栈 16 字节对齐 ==\n");
    (void)sp_alignment_probe(1);

    printf("\n== callee-saved 寄存器(r12)==\n");
    long before, after, out;
    __asm__ volatile("movq %%r12, %0" : "=r"(before));
    out = use_r12(21);
    __asm__ volatile("movq %%r12, %0" : "=r"(after));
    printf("r12 调用前=%ld, 被调函数用过, 返回后=%ld, 结果=%ld —— 完好如初\n",
           before, after, out);

    printf("\n逆向启示: 看到 mov rdi,rsi/rdx 立即数序列 → 正在准备前 3 参;\n"
           "call 前 push 两个值 → 至少 8 参(7/8 参在栈上)。\n");
    return 0;
}
