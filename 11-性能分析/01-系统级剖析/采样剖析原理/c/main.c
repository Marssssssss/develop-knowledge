/* 采样剖析原理 demo:C 版。
 *
 * 模型:setitimer(ITIMER_PROF, 10ms) 周期到期投递 SIGPROF,
 * 信号处理函数用 backtrace() 抓当前栈地址 -> 地址对哈希计数 ->
 * 结束时输出 folded 样本("0x...;0x... 计数" 形式,真实工具会先符号化)。
 *
 * 对应 README「原理详解」第 1-6 步;符号化延迟到事后
 * (addr2line / gdb),符合信号处理函数"只做最少的事"的原则。
 *
 * 编译: gcc -O2 -Wall -Wextra -g main.c -o sampler
 */
#define _GNU_SOURCE
#include <execinfo.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define MAX_FRAMES 64
#define MAX_STACKS 4096

/* 折叠样本表:字符串化的栈 -> 计数。demo 规模用线性数组即可。 */
struct sample {
    char key[MAX_FRAMES * 20];
    unsigned long count;
};
static struct sample g_table[MAX_STACKS];
static size_t g_table_len;
static unsigned long g_total;

/* 把一次抓到的帧地址数组折叠成 "0x..;0x.." 单行。 */
static void fold(const void *const *frames, int n) {
    char key[MAX_FRAMES * 20] = "";
    size_t off = 0;
    for (int i = 0; i < n; i++) {
        if (i > 0 && off + 1 < sizeof(key)) key[off++] = ';';
        off += (size_t)snprintf(key + off, sizeof(key) - off, "%p", frames[i]);
        if (off >= sizeof(key)) return;
    }
    for (size_t i = 0; i < g_table_len; i++) {
        if (strcmp(g_table[i].key, key) == 0) {
            g_table[i].count++;
            return;
        }
    }
    if (g_table_len < MAX_STACKS) {
        snprintf(g_table[g_table_len].key, sizeof(key), "%s", key);
        g_table[g_table_len].count = 1;
        g_table_len++;
    }
}

/* SIGPROF 处理函数:只抓地址、记账,不做符号化等慢操作。
 * 重入的 SIGPROF 会丢失(同类信号同时只能 pending 一个,man7 BUGS)。 */
static void on_sigprof(int sig, siginfo_t *si, void *uc) {
    (void)sig; (void)si; (void)uc;
    void *frames[MAX_FRAMES];
    int n = backtrace(frames, MAX_FRAMES);
    if (n > 1) fold(frames + 1, n - 1); /* 跳过信号处理函数自身帧 */
    g_total++;
}

static void install_profiler(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_flags = SA_SIGINFO | SA_RESTART;
    sa.sa_sigaction = on_sigprof;
    if (sigaction(SIGPROF, &sa, NULL) != 0) {
        perror("sigaction");
        _exit(1);
    }
    /* it_interval = 周期,it_value = 首次到期。均为 CPU 时间递减:
     * 进程不在 CPU 上时不计时 —— 这正是剖析想要的语义。 */
    struct itimerval it = {
        .it_interval = {0, 10 * 1000},   /* 10ms = 100Hz */
        .it_value    = {0, 10 * 1000},
    };
    if (setitimer(ITIMER_PROF, &it, NULL) != 0) {
        perror("setitimer");
        _exit(1);
    }
}

static void stop_profiler(void) {
    struct itimerval zero = {{0, 0}, {0, 0}};
    setitimer(ITIMER_PROF, &zero, NULL);
}

/* ---------------- 被剖析的负载 ---------------- */

static void leaf_hot(void) {
    volatile unsigned long acc = 0;
    for (int i = 0; i < 200000; i++) acc += (unsigned long)(i * i % 7);
    (void)acc;
}

static void mid_caller(void) { for (int i = 0; i < 50; i++) leaf_hot(); }

static void other_path(void) {
    volatile unsigned long acc = 0;
    for (int i = 0; i < 50000; i++) acc += (unsigned long)(i % 3);
    (void)acc;
}

int main(void) {
    puts("[sampler-c] 100Hz ITIMER_PROF, run 3s ...");
    install_profiler();
    time_t end = time(NULL) + 3;
    while (time(NULL) < end) {
        mid_caller();
        other_path();
    }
    stop_profiler();

    printf("[sampler-c] %lu samples, %zu distinct stacks\n\n", g_total, g_table_len);
    puts("---- folded 输出(地址栈,真实工具先 addr2line 符号化)----");
    for (size_t i = 0; i < g_table_len; i++) {
        double pct = g_total ? 100.0 * g_table[i].count / g_total : 0.0;
        printf("%5.1f%%  %s %lu\n", pct, g_table[i].key, g_table[i].count);
    }
    puts("\n[提示] 用 gdb ./sampler 或 addr2line -e ./sampler 0x... 把地址翻成函数名");
    return 0;
}
