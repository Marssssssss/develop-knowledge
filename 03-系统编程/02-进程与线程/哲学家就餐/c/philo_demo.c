/*
 * 哲学家就餐问题：3 种方案串行对比
 *
 *   STRATEGY_NAIVE        —— "先左后右"，极可能死锁
 *   STRATEGY_HIERARCHY    —— Resource Hierarchy，按筷子全局编号顺序，破除循环等待
 *   STRATEGY_TANENBAUM    —— Dijkstra+Tanenbaum 监视器方案，1 mutex + N condvar + state[]，
 *                            既无死锁也几乎无饥饿（5 个哲学家最多同时 ⌊N/2⌋=2 个进餐）
 *
 * 编译：gcc -O2 -Wall -Wextra -std=c11 -pthread philo_demo.c -o philo_demo
 * 运行：./philo_demo
 *
 * 输出示例：
 *   策略 [Naive (先左后右)] 结果：P0 吃了 X 次  ... 死锁出现...
 */

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define N 5

typedef enum { THINKING = 0, HUNGRY = 1, EATING = 2 } state_t;
typedef enum {
    STRATEGY_NAIVE = 0,
    STRATEGY_HIERARCHY,
    STRATEGY_TANENBAUM,
    STRATEGY_COUNT
} strategy_t;

static const char *strategy_names[STRATEGY_COUNT] = {
    "Naive (先左后右)",
    "Resource Hierarchy (筷子编号小者优先)",
    "Tanenbaum 监视器 (Dijkstra+Tanenbaum)"
};

/* ---- 共享资源 ---- */
static pthread_mutex_t forks[N];
static state_t          mp_state[N];          /* 仅 Tanenbaum 用 */
static pthread_mutex_t  mtx = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t   cv[N];                /* 仅 Tanenbaum 用 */
static strategy_t       g_strategy;
static volatile int     g_deadlock = 0;       /* Naive 死锁退出标志 */
static const int        G_MAX_MEALS = 6;      /* 每个策略每个哲学家最多 6 次进餐 */

/* ---- 工具 ---- */
static inline int left(int i)  { return (i + N - 1) % N; }
static inline int right(int i) { return (i + 1) % N; }

static void think(int i) {
    usleep(50000 + (rand() % 50000));   /* 50-100 ms */
}

static void eat(int i) {
    usleep(50000 + (rand() % 50000));   /* 50-100 ms */
}

/* ---------- Naive：先左后右，trylock + 重试。多次失败视为死锁 ---------- */
static int pickup_naive(int i) {
    int giveups = 0;
    int l = left(i), r = right(i);
    for (;;) {
        pthread_mutex_lock(&forks[l]);
        if (pthread_mutex_trylock(&forks[r]) == 0)
            return 0;                         /* 同时持两根 */
        /* 失败：放下左筷，短暂退避后重试 */
        pthread_mutex_unlock(&forks[l]);
        if (++giveups > 50) {                 /* 重试太多 → 互锁循环 */
            g_deadlock = 1;
            return -1;
        }
        usleep(10000 + (rand() % 30000));     /* 10-40 ms */
    }
}

static void putdown_naive(int i) {
    pthread_mutex_unlock(&forks[left(i)]);
    pthread_mutex_unlock(&forks[right(i)]);
}

/* ---------- Resource Hierarchy：按筷子编号小者优先 ---------- */
static int pickup_hier(int i) {
    int l = left(i), r = right(i);
    int first  = (l < r) ? l : r;
    int second = (l < r) ? r : l;
    pthread_mutex_lock(&forks[first]);
    pthread_mutex_lock(&forks[second]);
    return 0;
}

static void putdown_hier(int i) {
    pthread_mutex_unlock(&forks[left(i)]);
    pthread_mutex_unlock(&forks[right(i)]);
}

/* ---------- Tanenbaum 监视器 (Dijkstra 1965 + Tanenbaum 修订) ---------- */
static void test_forks(int i) {
    int l = left(i), r = right(i);
    /* 邻居都没在吃、且自己饥饿 → 可进餐 */
    if (mp_state[l] != EATING && mp_state[r] != EATING && mp_state[i] == HUNGRY) {
        mp_state[i] = EATING;
        pthread_cond_signal(&cv[i]);          /* 唤醒哲学家 i */
    }
}

static int pickup_tanenbaum(int i) {
    pthread_mutex_lock(&mtx);
    mp_state[i] = HUNGRY;
    test_forks(i);
    /* 谓词循环：处理 spurious wakeup、signal 丢失 */
    while (mp_state[i] != EATING)
        pthread_cond_wait(&cv[i], &mtx);      /* 原子释放 mutex + 阻塞 */
    pthread_mutex_unlock(&mtx);
    return 0;
}

static void putdown_tanenbaum(int i) {
    pthread_mutex_lock(&mtx);
    mp_state[i] = THINKING;
    test_forks(left(i));                      /* 邻居放下后通知其左右邻 */
    test_forks(right(i));
    pthread_mutex_unlock(&mtx);
}

/* ---------- 哲学家线程 ---------- */
typedef struct {
    int id;
    int meals;
} philo_arg_t;

static void *philosopher(void *arg) {
    philo_arg_t *p = (philo_arg_t *)arg;
    while (p->meals < G_MAX_MEALS && !g_deadlock) {
        think(p->id);
        int rc;
        switch (g_strategy) {
            case STRATEGY_NAIVE:     rc = pickup_naive(p->id);    break;
            case STRATEGY_HIERARCHY: rc = pickup_hier(p->id);     break;
            case STRATEGY_TANENBAUM: rc = pickup_tanenbaum(p->id); break;
            default:                 rc = -1;                     break;
        }
        if (rc != 0) return NULL;             /* Naive 死锁 → 线程退出 */
        eat(p->id);
        p->meals++;
        switch (g_strategy) {
            case STRATEGY_NAIVE:     putdown_naive(p->id);    break;
            case STRATEGY_HIERARCHY: putdown_hier(p->id);     break;
            case STRATEGY_TANENBAUM: putdown_tanenbaum(p->id); break;
            default:                                                       break;
        }
    }
    return NULL;
}

/* ---------- 演示驱动：每个策略跑一遍 N 个线程 ---------- */
static void run_strategy(strategy_t s) {
    g_strategy  = s;
    g_deadlock  = 0;

    /* 一次性初始化 */
    for (int i = 0; i < N; i++) {
        pthread_mutex_init(&forks[i], NULL);
        mp_state[i] = THINKING;
        pthread_cond_init(&cv[i], NULL);
    }

    /* 让 5 哲学家同时启动（Naive 死锁更容易触发） */
    pthread_t     th[N];
    philo_arg_t   args[N];
    pthread_mutex_lock(&mtx);                  /* 锁住，所有线程在 pickup 内阻塞起步 */
    for (int i = 0; i < N; i++) {
        args[i].id    = i;
        args[i].meals = 0;
        pthread_create(&th[i], NULL, philosopher, &args[i]);
    }
    usleep(20000);                            /* 20 ms 让所有线程都进 think */
    pthread_mutex_unlock(&mtx);                /* 同时放行 */

    for (int i = 0; i < N; i++) pthread_join(th[i], NULL);

    printf("\n=== [%s] 结果 ===\n", strategy_names[s]);
    if (g_deadlock) {
        printf(">> 死锁出现（Naive 高竞争下预期之中，重试次数过多判定为循环等待）\n");
    } else {
        for (int i = 0; i < N; i++) printf("    P%d 吃了 %d 次\n", i, args[i].meals);
    }

    for (int i = 0; i < N; i++) {
        pthread_mutex_destroy(&forks[i]);
        pthread_cond_destroy(&cv[i]);
    }
}

int main(void) {
    srand((unsigned)time(NULL));
    printf("================ 哲学家就餐问题演示 ================\n");
    for (strategy_t s = 0; s < STRATEGY_COUNT; s++)
        run_strategy(s);
    return 0;
}
