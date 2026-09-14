/*
 * mini_dbg.c —— 最小 ptrace 调试器：软件断点（int3）+ 单步执行
 *
 * 演示 gdb `break` / `stepi` 的三件底层工作：
 *   1) 用 PTRACE_PEEKTEXT/POKETEXT 把目标地址首字节换成 0xCC（INT3）埋下断点
 *   2) 命中 SIGTRAP 后：恢复原字节 → RIP 回退 1 字节 → 单步执行原指令 → 重新埋点
 *   3) 用 PTRACE_SINGLESTEP + PTRACE_GETREGS 统计并打印指令流
 *
 * 平台：**仅 Linux**（WSL2 亦可）。macOS 无 ptrace(2)，Windows 无此接口。
 * 编译：gcc -O0 -g -Wall -Wextra mini_dbg.c -o mini_dbg
 *       （不加 -pedantic：函数指针与 uintptr_t 的互转是 ISO C 之外的扩展，
 *         而"取函数地址当断点地址"正是调试器的常规做法）
 * 运行：./mini_dbg
 *
 * 参考：ptrace(2) man-pages 6.19；Eli Bendersky "How debuggers work: Part 1"
 */

#define _GNU_SOURCE   /* __WALL 在 glibc 中位于 __USE_GNU 保护下 */

#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/types.h>
#include <sys/user.h>
#include <sys/wait.h>
#include <unistd.h>

#define INT3 0xCCL
#define MAX_STEPS 48

typedef struct {
    uintptr_t addr;
    uint8_t   orig;  /* 被 0xCC 覆盖掉的原始字节 */
    int       armed;
} breakpoint_t;

/* ------------------------------------------------------------ 目标函数 */

/* 被跟踪的目标：故意写成几条清晰的算术指令，便于逐条单步观察 */
__attribute__((noinline)) static int target_function(int x)
{
    int a = x * 3;
    int b = a + 7;
    int c = b ^ 0x5A;
    int d = c - x;
    return d;
}

/* ---------------------------------------------------------- 断点原语 */

/*
 * 埋断点。返回 0 成功、-1 失败。
 * 注意：PTRACE_PEEKTEXT 成功时可以返回 -1，必须靠 errno 区分，见 README 坑 2。
 */
static int bp_arm(pid_t pid, breakpoint_t *bp)
{
    long word;

    errno = 0;
    word = ptrace(PTRACE_PEEKTEXT, pid, (void *)bp->addr, 0);
    if (word == -1 && errno != 0) {
        fprintf(stderr, "PEEKTEXT(0x%lx) 失败: %s\n",
                (unsigned long)bp->addr, strerror(errno));
        return -1;
    }
    bp->orig = (uint8_t)(word & 0xFF);

    word = (word & ~0xFFL) | INT3; /* 低字节换成 0xCC，其余保留 */
    if (ptrace(PTRACE_POKETEXT, pid, (void *)bp->addr, (void *)word) < 0) {
        fprintf(stderr, "POKETEXT 失败: %s\n", strerror(errno));
        return -1;
    }
    bp->armed = 1;
    return 0;
}

/* 摘除断点：把原始字节写回 */
static int bp_disarm(pid_t pid, breakpoint_t *bp)
{
    long word;

    errno = 0;
    word = ptrace(PTRACE_PEEKTEXT, pid, (void *)bp->addr, 0);
    if (word == -1 && errno != 0)
        return -1;
    word = (word & ~0xFFL) | (long)bp->orig;
    if (ptrace(PTRACE_POKETEXT, pid, (void *)bp->addr, (void *)word) < 0)
        return -1;
    bp->armed = 0;
    return 0;
}

/*
 * 断点命中后的修复流程。顺序不可颠倒（见 README 坑 3、4）：
 *   恢复原字节 → RIP 回退 → 单步执行原指令 → 重新埋点
 */
static int bp_replay_once(pid_t pid, breakpoint_t *bp, int *status_out)
{
    struct user_regs_struct regs;

    /* 1) 恢复原字节，否则单步会再次踩到 0xCC */
    if (bp_disarm(pid, bp) < 0)
        return -1;

    /* 2) INT3 命中时 RIP 已指向 0xCC 之后，回退到指令首字节 */
    if (ptrace(PTRACE_GETREGS, pid, 0, &regs) < 0)
        return -1;
    printf("  [命中] RIP=0x%llx (回退前)  rdi=%lld  ← 第一个整型参数\n",
           (unsigned long long)regs.rip, (long long)regs.rdi);
    regs.rip = (unsigned long long)bp->addr;
    if (ptrace(PTRACE_SETREGS, pid, 0, &regs) < 0)
        return -1;

    /* 3) 只执行那一条原始指令 */
    if (ptrace(PTRACE_SINGLESTEP, pid, 0, 0) < 0)
        return -1;
    if (waitpid(pid, status_out, __WALL) < 0)
        return -1;

    /* 4) 重新埋点，等待下一次命中 */
    if (bp_arm(pid, bp) < 0)
        return -1;
    return 0;
}

/* ------------------------------------------------------------- 被跟踪端 */

static void run_target(void)
{
    if (ptrace(PTRACE_TRACEME, 0, 0, 0) < 0) {
        fprintf(stderr, "TRACEME 失败: %s\n", strerror(errno));
        _exit(1);
    }
    /* 主动停一下，让父进程拿到第一个 signal-delivery-stop */
    if (raise(SIGSTOP) != 0)
        _exit(1);

    printf("[target] 即将调用 target_function(3)\n");
    printf("[target] 返回值 = %d\n", target_function(3));
    _exit(0);
}

/* --------------------------------------------------------------- 跟踪端 */

static void run_tracer(pid_t pid)
{
    int             status = 0;
    breakpoint_t    bp;
    struct user_regs_struct regs;
    int             steps = 0;

    memset(&bp, 0, sizeof bp);

    /* 阶段 0：等待 target 的 SIGSTOP */
    if (waitpid(pid, &status, __WALL) < 0) {
        perror("waitpid");
        return;
    }
    if (!WIFSTOPPED(status)) {
        fprintf(stderr, "预期 STOPPED，实际 status=0x%x\n", status);
        return;
    }
    printf("[tracer] target 已停在入口，信号=%d (SIGSTOP=%d)\n",
           WSTOPSIG(status), SIGSTOP);

    /* 阶段 1：在 target_function 入口埋断点，然后放手运行 */
    bp.addr = (uintptr_t)(void *)&target_function;
    printf("[tracer] 在 target_function (0x%lx) 埋入 0xCC\n",
           (unsigned long)bp.addr);
    if (bp_arm(pid, &bp) < 0)
        return;

    if (ptrace(PTRACE_CONT, pid, 0, 0) < 0) {
        perror("CONT");
        return;
    }
    if (waitpid(pid, &status, __WALL) < 0)
        return;
    if (!WIFSTOPPED(status) || WSTOPSIG(status) != SIGTRAP) {
        fprintf(stderr, "预期断点 SIGTRAP，实际 status=0x%x\n", status);
        return;
    }
    printf("[tracer] 断点命中（SIGTRAP=%d）\n", SIGTRAP);

    /* 阶段 2：修复 + 单步 + 复位（这是 gdb 命中 break 后做的事） */
    if (bp_replay_once(pid, &bp, &status) < 0) {
        perror("bp_replay_once");
        return;
    }
    printf("[tracer] 原指令已单步执行并复位断点\n");

    /* 阶段 3：断点已摘除的状态下连续单步，抓一段指令流 */
    if (bp_disarm(pid, &bp) < 0)
        return;
    printf("[tracer] 开始单步（上限 %d 步，RIP 离开函数体即停）\n", MAX_STEPS);
    while (steps < MAX_STEPS) {
        uintptr_t rip;

        if (ptrace(PTRACE_SINGLESTEP, pid, 0, 0) < 0)
            break;
        if (waitpid(pid, &status, __WALL) < 0)
            break;
        if (!WIFSTOPPED(status))
            break;

        if (ptrace(PTRACE_GETREGS, pid, 0, &regs) < 0)
            break;
        rip = (uintptr_t)regs.rip;
        steps++;
        printf("  step %2d: RIP=0x%llx  rax=0x%llx  rbx=0x%llx\n",
               steps, (unsigned long long)rip,
               (unsigned long long)regs.rax, (unsigned long long)regs.rbx);

        /* 跑出函数范围就停（±0x200 是保守窗口） */
        if (rip < bp.addr || rip > bp.addr + 0x200) {
            printf("  → RIP 已离开函数体，停止单步\n");
            break;
        }
    }

    /* 阶段 4：放行到退出 */
    if (ptrace(PTRACE_CONT, pid, 0, 0) == 0)
        waitpid(pid, &status, __WALL);

    if (WIFEXITED(status))
        printf("[tracer] target 退出码 = %d，共单步 %d 条指令\n",
               WEXITSTATUS(status), steps);
    else if (WIFSIGNALED(status))
        printf("[tracer] target 被信号 %d 终止\n", WTERMSIG(status));
}

int main(void)
{
    pid_t pid = fork();

    if (pid < 0) {
        perror("fork");
        return 1;
    }
    if (pid == 0) {
        run_target();   /* 不返回 */
        return 1;
    }
    run_tracer(pid);
    return 0;
}
