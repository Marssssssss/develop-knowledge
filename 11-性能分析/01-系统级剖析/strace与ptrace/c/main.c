/* strace 与 ptrace demo:C 版 —— 迷你 strace。
 *
 * 模型(man7 ptrace(2)):
 *   1. fork 子进程 -> PTRACE_TRACEME -> raise(SIGSTOP) -> execve 内置负载;
 *   2. 父进程 PTRACE_SETOPTIONS(PTRACE_O_TRACESYSGOOD);
 *   3. 循环 PTRACE_SYSCALL + waitpid(__WALL):
 *        WSTOPSIG == (SIGTRAP|0x80)  => 系统调用边界(enter/exit 自行配对);
 *   4. enter-stop 读 orig_rax(调用号)+ rdi/rsi/rdx(参数),
 *      exit-stop 读 rax(返回值),统计耗时与调用次数(类似 strace -c)。
 *
 * 编译: gcc -O2 -Wall -Wextra -g main.c -o ministrace && ./ministrace
 */
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/types.h>
#include <sys/user.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define MAX_SYSCALLS 512

/* x86-64 常用系统调用号(man2 syscall;仅 demo 所需子集) */
static const struct { long nr; const char *name; } SYSCALL_NAMES[] = {
    {0, "read"}, {1, "write"}, {2, "open"}, {3, "close"}, {9, "mmap"},
    {10, "mprotect"}, {11, "munmap"}, {12, "brk"}, {21, "access"},
    {39, "getpid"}, {56, "clone"}, {57, "fork"}, {59, "execve"},
    {60, "exit"}, {63, "uname"}, {158, "arch_prctl"},
    {218, "set_tid_address"}, {231, "exit_group"},
    {257, "openat"}, {302, "prlimit64"}, {318, "getrandom"},
};
static const char *syscall_name(long nr) {
    for (size_t i = 0; i < sizeof(SYSCALL_NAMES) / sizeof(SYSCALL_NAMES[0]); i++)
        if (SYSCALL_NAMES[i].nr == nr) return SYSCALL_NAMES[i].name;
    return "unknown";
}

/* 每个系统调用的次数与累计时间(strace -c 的最小子集) */
struct tally { unsigned long n; double us; };
static struct tally g_tally[MAX_SYSCALLS];
static double now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e6 + ts.tv_nsec / 1e3;
}

/* 被追踪的负载:做几次 write/getpid/uname(系统调用可见) */
static void child_load(void) {
    char buf[64];
    struct utsname u;
    for (int i = 0; i < 3; i++) {
        ssize_t n = read(STDIN_FILENO, buf, 0);     /* read(fd, buf, 0) */
        (void)n;
        if (uname(&u) == 0) {
            int len = snprintf(buf, sizeof(buf), "[%d] sysname=%s\n",
                               getpid(), u.sysname);
            if (write(STDOUT_FILENO, buf, (size_t)len) < 0) _exit(1);
        }
    }
}

int main(void) {
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 1; }
    if (pid == 0) {
        /* tracee:PTRACE_TRACEME 后 raise(SIGSTOP) 让父进程就位(man7 推荐流程) */
        if (ptrace(PTRACE_TRACEME, 0L, 0L, 0L) != 0) { perror("TRACEME"); _exit(1); }
        raise(SIGSTOP);
        child_load();
        _exit(0);
    }

    /* tracer:等待初始 stop */
    int status;
    if (waitpid(pid, &status, __WALL) < 0) { perror("waitpid"); return 1; }
    if (!WIFSTOPPED(status)) { fprintf(stderr, "child did not stop\n"); return 1; }

    /* TRACESYSGOOD:syscall 陷阱报 SIGTRAP|0x80,可靠区分普通 SIGTRAP */
    if (ptrace(PTRACE_SETOPTIONS, pid, 0L, (void *)(long)PTRACE_O_TRACESYSGOOD) != 0) {
        perror("SETOPTIONS"); return 1;
    }

    printf("%-5s %-18s %s\n", "PID", "SYSCALL", "args/retval");
    int in_syscall = 0;                 /* enter/exit 配对由 tracer 自己维护 */
    long cur_nr = 0;
    double enter_ts = 0.0;
    unsigned long total = 0;

    for (;;) {
        if (ptrace(PTRACE_SYSCALL, pid, 0L, 0L) != 0) {
            if (errno == ESRCH) break;  /* tracee 已死(WNOHANG 陷阱,man7) */
            perror("PTRACE_SYSCALL"); break;
        }
        if (waitpid(pid, &status, __WALL) <= 0) break;
        if (WIFEXITED(status)) {
            printf("+++ exited with %d +++\n", WEXITSTATUS(status));
            break;
        }
        if (!WIFSTOPPED(status)) continue;
        int sig = WSTOPSIG(status);
        if (sig == (SIGTRAP | 0x80)) {                 /* 系统调用边界 */
            struct user_regs_struct regs;
            if (ptrace(PTRACE_GETREGS, pid, 0L, &regs) != 0) {
                perror("GETREGS"); break;
            }
            if (!in_syscall) {                          /* enter-stop */
                cur_nr = (long)regs.orig_rax;
                enter_ts = now_us();
                printf("%-5d %-18s 0x%llx, 0x%llx, 0x%llx",
                       pid, syscall_name(cur_nr),
                       (unsigned long long)regs.rdi,
                       (unsigned long long)regs.rsi,
                       (unsigned long long)regs.rdx);
            } else {                                     /* exit-stop */
                double dt = now_us() - enter_ts;
                long ret = (long)regs.rax;
                printf(" = %ld (%.1f us)\n", ret, dt);
                if (cur_nr >= 0 && cur_nr < MAX_SYSCALLS) {
                    g_tally[cur_nr].n++;
                    g_tally[cur_nr].us += dt;
                }
                total++;
            }
            in_syscall = !in_syscall;
        } else if (sig == SIGSTOP) {
            /* 首个 SIGSTOP 已在循环外消费;执行中再收到则原样投递 */
            ptrace(PTRACE_SYSCALL, pid, 0L, (void *)(long)sig);
            continue;
        } else {
            /* 其余信号原样投递(signal-delivery-stop 的默认处理) */
            if (ptrace(PTRACE_SYSCALL, pid, 0L, (void *)(long)sig) != 0) break;
        }
    }

    printf("\n---- 汇总(类似 strace -c)----\n");
    printf("%-18s %10s %14s\n", "syscall", "calls", "total_us");
    for (size_t i = 0; i < MAX_SYSCALLS; i++) {
        if (g_tally[i].n) {
            printf("%-18s %10lu %14.1f\n", syscall_name((long)i),
                   g_tally[i].n, g_tally[i].us);
        }
    }
    printf("\n%lu syscalls traced. 注意: 每个 syscall = 2 次 ptrace-stop + "
           "2 次上下文切换 —— 这就是 strace 慢 62 倍的原因(Gregg dd 实测)。\n",
           total);
    return 0;
}
