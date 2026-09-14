/*
 * stack_layout.c —— 观察 x86-64（System V AMD64 ABI）真实栈帧与 rbp 链
 *
 * 本文件**不含任何 exploit**：目的是把"栈帧里到底有什么、返回地址在哪、
 * 帧指针链怎么走、canary 长什么样"变成可直接打印的事实。
 * 理解了这些偏移，才谈得上理解缓冲区溢出为什么能改写返回地址。
 *
 * 编译（建议关优化 + 保留帧指针，否则 rbp 会被复用为通用寄存器）：
 *   gcc -O0 -fno-omit-frame-pointer -fstack-protector-strong \
 *       -Wall -Wextra -pedantic stack_layout.c -o stack_layout
 *
 * 参考：Eli Bendersky "Stack frame layout on x86-64"；Aleph1, Phrack #49/14
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if defined(__x86_64__) || defined(_M_X64)
#  define ARCH_X86_64 1
#else
#  define ARCH_X86_64 0
#endif

/* 用编译器内建函数取当前帧指针（等价于读 rbp 寄存器） */
#if defined(__GNUC__)
#  define GET_FP() __builtin_frame_address(0)
#else
#  define GET_FP() ((void *)0)
#endif

#define MAX_FRAMES 8

/* ---------------------------------------------------------------- 帧链回溯 */

/*
 * 手工走 rbp 链 —— 这就是 gdb `bt` 在无 DWARF 信息时的做法。
 * 约定（System V AMD64）：
 *   [rbp + 0] = 保存的上层 rbp (SFP)
 *   [rbp + 8] = 返回地址 (RET)
 */
static void walk_frames(void *rbp, int depth)
{
    int i;
    for (i = 0; i < depth; i++) {
        void **frame;
        void  *next_rbp;
        void  *ret_addr;

        if (rbp == NULL || (uintptr_t)rbp < 0x1000 || ((uintptr_t)rbp & 0x7) != 0)
            break;

        frame    = (void **)rbp;
        next_rbp = frame[0];
        ret_addr = frame[1];

        printf("  frame %d: rbp=%p  save_rbp=%p  ret=%p\n",
               i, rbp, next_rbp, ret_addr);

        /* 链必须严格向高地址走，否则说明 rbp 被复用/数据损坏 */
        if (next_rbp != NULL && (uintptr_t)next_rbp <= (uintptr_t)rbp) {
            printf("  frame %d: 链断裂（上层 rbp 未高于当前 rbp）\n", i + 1);
            break;
        }
        rbp = next_rbp;
    }
}

/* ------------------------------------------------- 观察参数与局部变量的偏移 */

/*
 * 6 个参数：全部走寄存器，栈上不会出现它们。
 * 第 7、8 个参数：出现在 [rbp + 16]、[rbp + 24]。
 */
__attribute__((noinline)) static long probe_frame(long a1, long a2, long a3,
                                                  long a4, long a5, long a6,
                                                  long a7, long a8)
{
#if ARCH_X86_64
    void        *fp    = GET_FP();
    long        *base  = (long *)fp;
    char         local[32];
    volatile int marker = 0x5A5A;

    printf("\n=== probe_frame 的帧内布局（rbp = %p）===\n", fp);
    printf("  [rbp + 24] 第 8 个参数      = %ld  (栈传参)\n", base[3]);
    printf("  [rbp + 16] 第 7 个参数      = %ld  (栈传参)\n", base[2]);
    printf("  [rbp +  8] 返回地址         = %p   <-- 溢出攻击改写的就是这里\n",
           (void *)base[1]);
    printf("  [rbp +  0] 保存的上层 rbp   = %p\n", (void *)base[0]);
    printf("  [rbp -  8] 局部变量 marker  = 0x%x\n", marker);
    printf("  [rbp - 40] 局部缓冲区 local = %p (32 字节)\n", (void *)local);
    printf("  参数 a1..a6 在寄存器 rdi/rsi/rdx/rcx/r8/r9 中，栈上无副本：%ld %ld %ld %ld %ld %ld\n",
           a1, a2, a3, a4, a5, a6);

    printf("\n  local 缓冲区与返回地址的距离 = %ld 字节\n",
           (long)((char *)&base[1] - local));
    printf("  → 写满 local 后继续写 %ld 字节即可抵达返回地址\n",
           (long)((char *)&base[1] - local) + 8);
#else
    (void)a1; (void)a2; (void)a3; (void)a4;
    (void)a5; (void)a6; (void)a7; (void)a8;
    printf("\n非 x86-64 平台：本 demo 的偏移结论仅适用于 System V AMD64 ABI\n");
#endif

    /* 用易失写防止编译器把整个函数优化掉 */
    memset(local, 0, sizeof local);
    local[0] = (char)(a1 + a2);
    return a7 + a8 + (long)local[0];
}

/* --------------------------------------------------------------- canary 观察 */

/* 逐字节经 volatile 指针读取一个字：既避免编译期数组越界告警，也不依赖类型双关 */
static long read_word(const volatile char *p)
{
    long     w = 0;
    unsigned i;
    for (i = 0; i < sizeof w; i++)
        w |= (long)(unsigned char)p[i] << (8 * i);
    return w;
}

/*
 * 开启 -fstack-protector-strong 后，编译器会在缓冲区与 rbp 之间插入一个
 * 从 %fs:0x28 载入的哨兵值；函数返回前校验，不匹配即 __stack_chk_fail。
 */
__attribute__((noinline)) static int canary_probe(void)
{
    char           buf[32];
    volatile char *ro = buf;   /* 故意越过缓冲区上界读取，用于观察 canary 位置 */
    int            i;
    for (i = 0; i < 32; i++)
        buf[i] = (char)i;
    printf("\n=== canary_probe 缓冲区上方 16 字节 ===\n");
    for (i = 32; i < 48; i += 8) {
        long w = read_word(ro + i);
        printf("  [buf + %2d] = 0x%016lx%s\n", i, (unsigned long)w,
               i == 32 ? "   <-- canary 大概率在此" : "   <-- 再往上是保存的 rbp");
    }
    return buf[0] + buf[31];
}

/* ----------------------------------------------------------- red zone 演示 */

/*
 * red zone：rsp 以下 128 字节保证不被信号/中断处理程序破坏，
 * 叶子函数可直接使用，无需调整 rsp。但**一旦发生函数调用**它就会被踩。
 */
__attribute__((noinline)) static void red_zone_leaf(void)
{
    void *sp = NULL;
#if ARCH_X86_64
    __asm__ volatile ("mov %%rsp, %0" : "=r"(sp));
    printf("\n=== red zone ===\n");
    printf("  叶子函数 rsp = %p\n", sp);
    printf("  [rsp -  8..-128] 这 128 字节可直接用作临时存储，无需 sub rsp\n");
    printf("  [rsp +  8] 是返回地址 %p（写到这里就越界了）\n",
           (void *)((char **)sp)[1]);
#else
    (void)sp;
#endif
}

/* ------------------------------------------------------------------- main */

static int level3(int depth);
static int level2(int depth) { return level3(depth) + 1; }
static int level1(int depth) { return level2(depth) + 1; }

__attribute__((noinline)) static int level3(int depth)
{
    void *fp = GET_FP();
    printf("\n=== 从 level3 向上回溯调用栈（depth=%d）===\n", depth);
    walk_frames(fp, depth);
    return 0;
}

int main(void)
{
    printf("=== 架构 ===\n");
#if ARCH_X86_64
    printf("  x86_64：call 压入 8 字节返回地址，red zone 128 字节，"
           "前 6 个整型参数走寄存器\n");
#else
    printf("  非 x86_64：偏移与 red zone 结论不适用\n");
#endif
    printf("  sizeof(void*)=%zu  sizeof(long)=%zu\n", sizeof(void *), sizeof(long));

    /* 传 8 个参数，正好让第 7、8 个落到栈上 */
    volatile long sink = probe_frame(1, 2, 3, 4, 5, 6, 7, 8);
    printf("\nprobe_frame 返回值 = %ld\n", sink);

    sink += canary_probe();
    red_zone_leaf();
    sink += level1(4);
    printf("\n(结果汇总 %ld，仅用于阻止优化)\n", sink);
    return 0;
}
