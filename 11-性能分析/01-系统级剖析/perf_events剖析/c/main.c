/* perf_events 剖析 demo:C 版 —— 两个软件模型:
 *
 * 1. rdtsc 读取 TSC(周期计数):演示 "cycles 计数" 与墙钟时间的本质差异
 *    —— cycles 是 CPU 频率维度上的刻度,同一循环不同频率下时间不同但
 *    cycles 计数接近(现代系统 TSC 为 constant/invariant,此处只取原理)。
 *
 * 2. PMC 溢出阈值采样模型:perf record -e <event> -c 10000 的核心机制 ——
 *    计数器每溢出 10000 次(阈值)才中断一次内核,采集 IP/栈,
 *    避免逐事件采样的高开销(对照 README「事件计数触发采样」)。
 *
 * 编译: gcc -O2 -Wall -Wextra main.c -o pmu
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#if defined(__x86_64__) || defined(__i386__)
static inline uint64_t rdtsc(void) {
    uint32_t lo, hi;
    __asm__ __volatile__("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}
#define HAVE_RDTSC 1
#else
#define HAVE_RDTSC 0
#endif

/* 被计数的负载:三种行为不同的循环(分支多/访存规律/纯算术) */
static void workload(uint64_t *branch_miss_like, uint64_t *mem_touch, uint64_t *arith) {
    uint32_t acc = 0;
    for (int i = 0; i < 5 * 1000 * 1000; i++) {
        acc += (i % 3 == 0) ? i : (i ^ 0x5a5a);   /* 分支模式 */
        if ((i & 1023) == 0) *branch_miss_like += (i % 7);
        if ((i & 255) == 0) (*mem_touch)++;        /* 规律访存 */
        acc = acc * 31 + i;                          /* 纯算术 */
    }
    *arith += acc;
}

/* ---- 模型一:rdtsc 周期计数 vs 墙钟时间 ---- */
static void demo_tsc(void) {
#if HAVE_RDTSC
    uint64_t b, br = 0, mt = 0, ar = 0;
    b = rdtsc();
    workload(&br, &mt, &ar);
    uint64_t cycles = rdtsc() - b;
    clock_t t0 = clock();
    workload(&br, &mt, &ar);
    double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
    printf("[tsc]    loop#1: %llu cycles\n", (unsigned long long)cycles);
    printf("[tsc]    loop#2: %.3f cpu-seconds\n", secs);
    if (secs > 0) {
        printf("[tsc]    估计频率 = %.2f GHz (cycles / cpu-time)\n",
               (double)cycles / secs / 1e9);
    }
    printf("        cycles 是频率维度的刻度 —— perf stat 输出里 cycles 行的 "
           "'2.942 GHz' 就是这么除出来的\n\n");
#else
    (void)demo_tsc; printf("[tsc]    本架构无 rdtsc,跳过\n\n");
#endif
}

/* ---- 模型二:-c 10000 阈值溢出采样 ----
 * 软件模型:每个"事件"让 PMC +1,达到 counter_period 触发一次溢出中断,
 * 内核才抓 IP/栈写环形缓冲。对照:
 *   perf record -e L1-dcache-load-misses -c 10000 -ag
 */
typedef struct {
    const char *event;
    uint64_t counter_period;   /* -c 参数 */
    uint64_t count;            /* 事件累计 */
    uint64_t samples;          /* 采样(溢出中断)次数 */
} pmc_model;

static void pmc_event(pmc_model *m) {
    if (++m->count >= m->counter_period) {  /* PMC 溢出 -> 中断内核 */
        m->count = 0;                        /* 重装计数器 */
        m->samples++;                       /* 抓 IP/栈,写环形缓冲 */
    }
}

static void demo_threshold_sampling(void) {
    pmc_model models[] = {
        {"L1-dcache-load-misses", 1,     0, 0},      /* -c 1:全量采样 */
        {"L1-dcache-load-misses", 100,   0, 0},      /* -c 100 */
        {"L1-dcache-load-misses", 10000, 0, 0},      /* -c 10000 */
    };
    const uint64_t events = 1000000;                  /* 模拟百万次事件 */

    for (size_t i = 0; i < sizeof(models) / sizeof(models[0]); i++) {
        for (uint64_t e = 0; e < events; e++) {
            pmc_event(&models[i]);                    /* 每事件一次 */
        }
    }
    printf("[sample] %llu 个事件, 不同 -c 阈值下的采样(中断)次数:\n",
           (unsigned long long)events);
    for (size_t i = 0; i < sizeof(models) / sizeof(models[0]); i++) {
        double rate = 100.0 * (double)models[i].samples / (double)events;
        printf("  -c %-6llu %-24s -> %llu 次中断 (%.3f%% 事件被采样)\n",
               (unsigned long long)models[i].counter_period, models[i].event,
               (unsigned long long)models[i].samples, rate);
    }
    printf("        由处理器实现:达到阈值才中断内核,避免逐事件采样的高开销\n");
    printf("        (对照 perf record 默认:context-switches 的 sample_freq=4000 即子集采样)\n\n");
}

int main(void) {
    puts("== perf_events 软件模型 demo ==\n");
    demo_tsc();
    demo_threshold_sampling();
    puts("提示: 软件事件(context-switches 等)有默认采样周期;");
    puts("      要全量记录用 -c 1;先 perf stat 计数再决定是否采样。");
    return 0;
}
