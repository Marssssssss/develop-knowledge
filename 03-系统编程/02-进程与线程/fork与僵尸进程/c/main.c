/*
 * fork 与僵尸进程:C 实现(仅 Linux 可运行)
 *
 * 四个场景:
 *   1. stdio 缓冲区被 fork 复制:子进程 exit(3) → 输出双份;_exit(2) → 单份
 *   2. 僵尸产生与 WNOHANG 轮询收尸(窗口期可 ps 看到 Z 状态)
 *   3. 退出状态位编码:正常退出 / 信号致死 / 低 8 位截断
 *   4. SIGCHLD 设为 SIG_IGN:子进程不变僵尸,waitpid → ECHILD
 *
 * 依据:man7.org fork(2)、wait(2)(见 README 参考资料)
 */
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

/* 演示环境的极简断言:失败立即终止,退出码 1 */
static void check(int cond, const char *label)
{
    if (cond) {
        printf("PASS: %s\n", label);
    } else {
        printf("FAIL: %s\n", label);
        exit(EXIT_FAILURE);
    }
}

/* ---------- 场景 1:stdio 缓冲区复制 → 输出双份 ---------- */
static int count_char(const char *path, char c)
{
    FILE *f = fopen(path, "r");
    if (f == NULL)
        return -1;
    int n = 0;
    int ch;
    while ((ch = fgetc(f)) != EOF) {
        if (ch == (unsigned char)c)
            n++;
    }
    fclose(f);
    return n;
}

/* child_exit3:1 = 子进程用 exit(3) 终止,0 = 用 _exit(2) 终止 */
static void demo_stdio_dup(int child_exit3)
{
    /* 文件是全缓冲(stdout 接终端是行缓冲,故改用文件使行为确定) */
    const char *path = child_exit3 ? "stdio_exit3.txt" : "stdio__exit2.txt";
    FILE *f = fopen(path, "w");
    if (f == NULL) {
        perror("fopen");
        exit(EXIT_FAILURE);
    }
    setvbuf(f, NULL, _IOFBF, 8192);   /* 强制全缓冲 */
    fprintf(f, "X");                  /* 不换行、不 flush → 停留在缓冲区 */

    pid_t pid = fork();
    if (pid == -1) {
        perror("fork");
        exit(EXIT_FAILURE);
    }
    if (pid == 0) {
        /* 子进程:地址空间副本里带着同一份未刷缓冲区 */
        if (child_exit3)
            exit(EXIT_SUCCESS);       /* exit(3):刷缓冲 → 写出第一个 X */
        _exit(EXIT_SUCCESS);          /* _exit(2):不刷 → 不写 */
    }

    if (waitpid(pid, NULL, 0) == -1) {
        perror("waitpid");
        exit(EXIT_FAILURE);
    }
    fflush(f);                       /* 父进程刷自己的那份 */
    fclose(f);

    int n = count_char(path, 'X');
    if (child_exit3)
        check(n == 2, "stdio: child exit(3) duplicates buffered output");
    else
        check(n == 1, "stdio: child _exit(2) keeps single output");
    remove(path);
}

/* ---------- 场景 2:僵尸与 WNOHANG 轮询 ---------- */
static void demo_zombie(void)
{
    pid_t pid = fork();
    if (pid == -1) {
        perror("fork");
        exit(EXIT_FAILURE);
    }
    if (pid == 0) {
        printf("[zombie] child %d will _exit(42) after 2s\n", (int)getpid());
        sleep(2);
        _exit(42);
    }

    int wstatus = 0;
    pid_t r = waitpid(pid, &wstatus, WNOHANG);
    if (r == 0) {
        printf("PASS: WNOHANG returns 0 while child still running\n");
    } else if (r == pid) {
        printf("PASS: WNOHANG returns pid (child raced to exit)\n");
    } else {
        check(0, "waitpid WNOHANG unexpected result");
    }

    puts("[zombie] parent sleeps 3s without wait "
         "-- run: ps -o pid,ppid,stat,cmd -C fork_demo");
    sleep(3);                         /* 此窗口内子进程为 Z 状态 */

    r = waitpid(pid, &wstatus, WNOHANG);
    check(r == pid, "waitpid reaps zombie child");
    check(WIFEXITED(wstatus), "WIFEXITED(child _exit(42))");
    check(WEXITSTATUS(wstatus) == 42, "WEXITSTATUS == 42");
}

/* ---------- 场景 3:退出状态位编码 ---------- */
static void demo_status_encoding(void)
{
    /* 3a. 信号致死 */
    pid_t pid = fork();
    if (pid == 0) {
        raise(SIGKILL);               /* 子进程被信号杀死 */
    }
    int wstatus = 0;
    check(waitpid(pid, &wstatus, 0) == pid, "reap signal-killed child");
    check(WIFSIGNALED(wstatus), "WIFSIGNALED(raise(SIGKILL))");
    check(!WIFEXITED(wstatus), "WIFEXITED is false for signal death");
    check(WTERMSIG(wstatus) == SIGKILL, "WTERMSIG == SIGKILL");

    /* 3b. 退出参数只保留低 8 位:300 = 0x12C → 0x2C = 44 */
    pid = fork();
    if (pid == 0) {
        _exit(300);
    }
    check(waitpid(pid, &wstatus, 0) == pid, "reap _exit(300) child");
    check(WIFEXITED(wstatus) && WEXITSTATUS(wstatus) == 44,
          "WEXITSTATUS(_exit(300)) == 44 (low 8 bits)");
}

/* ---------- 场景 4:SIGCHLD = SIG_IGN ---------- */
static void demo_sigchld_ign(void)
{
    if (signal(SIGCHLD, SIG_IGN) == SIG_ERR) {
        perror("signal");
        exit(EXIT_FAILURE);
    }
    pid_t pid = fork();
    if (pid == 0) {
        _exit(7);
    }
    sleep(1);                         /* 确保子进程已终止且被自动收尸 */

    errno = 0;
    pid_t r = waitpid(pid, NULL, 0);
    check(r == -1 && errno == ECHILD,
          "SIG_IGN: no zombie left, waitpid fails with ECHILD");

    if (signal(SIGCHLD, SIG_DFL) == SIG_ERR) {
        perror("signal");
        exit(EXIT_FAILURE);
    }
}

int main(void)
{
    puts("== 1. stdio buffer duplication ==");
    demo_stdio_dup(0);               /* 子进程 _exit */
    demo_stdio_dup(1);               /* 子进程 exit  */

    puts("== 2. zombie & WNOHANG ==");
    demo_zombie();

    puts("== 3. status encoding ==");
    demo_status_encoding();

    puts("== 4. SIGCHLD SIG_IGN ==");
    demo_sigchld_ign();

    puts("all fork/zombie scenarios passed");
    return 0;
}
