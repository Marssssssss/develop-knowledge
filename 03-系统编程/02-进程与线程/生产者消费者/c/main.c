/*
 * 生产者-消费者:C 实现(pthread mutex + 两个条件变量)
 *
 * 依据 man7 pthread_cond_wait(3):等待必须配 while 谓词循环;
 * 修改共享数据与 signal 均在持锁状态下进行。
 * 消费者终止用哨兵(-1):生产者全部结束后由 main 投放,每消费者一个,
 * 避免"检查总数再取"的空缓冲死锁竞态。
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

#define BUF_CAP 4
#define N_PROD 2
#define N_CONS 2
#define ITEMS_PER_PROD 20
#define TOTAL_ITEMS (N_PROD * ITEMS_PER_PROD)
#define SENTINEL (-1)

/* 有界环形缓冲:head/tail/count 分离,count 避免 head==tail 的空满二义 */
typedef struct {
    int slots[BUF_CAP];
    int head, tail, count;
    pthread_mutex_t mu;
    pthread_cond_t not_full;   /* 谓词:count < BUF_CAP */
    pthread_cond_t not_empty;  /* 谓词:count > 0 */
} bounded_buf;

static bounded_buf bb = {
    .mu = PTHREAD_MUTEX_INITIALIZER,
    .not_full = PTHREAD_COND_INITIALIZER,
    .not_empty = PTHREAD_COND_INITIALIZER,
};

static int seen[TOTAL_ITEMS];  /* 每个元素被消费次数,终局应恰为 1 */

static void check(int cond, const char *label)
{
    if (cond) {
        printf("PASS: %s\n", label);
    } else {
        printf("FAIL: %s\n", label);
        exit(EXIT_FAILURE);
    }
}

static void buf_put(int v)
{
    pthread_mutex_lock(&bb.mu);
    while (bb.count == BUF_CAP)               /* while 而非 if:防虚假唤醒 */
        pthread_cond_wait(&bb.not_full, &bb.mu);
    bb.slots[bb.tail] = v;
    bb.tail = (bb.tail + 1) % BUF_CAP;
    bb.count++;
    pthread_cond_signal(&bb.not_empty);      /* 持锁时发信号 */
    pthread_mutex_unlock(&bb.mu);
}

static int buf_get(void)
{
    pthread_mutex_lock(&bb.mu);
    while (bb.count == 0)                    /* 空则挂起在 not_empty 上 */
        pthread_cond_wait(&bb.not_empty, &bb.mu);
    int v = bb.slots[bb.head];
    bb.head = (bb.head + 1) % BUF_CAP;
    bb.count--;
    pthread_cond_signal(&bb.not_full);
    pthread_mutex_unlock(&bb.mu);
    return v;
}

static void *producer_main(void *arg)
{
    int id = (int)(long)arg;                 /* id 经整数值直接编码传参 */
    for (int j = 0; j < ITEMS_PER_PROD; j++)
        buf_put(id * ITEMS_PER_PROD + j);
    return NULL;
}

static void *consumer_main(void *arg)
{
    (void)arg;
    for (;;) {
        int v = buf_get();
        if (v == SENTINEL)
            return NULL;                     /* 哨兵:该消费者到此退出 */
        seen[v]++;                          /* 只有真数据才计数 */
    }
    return NULL;
}

int main(void)
{
    pthread_t prod[N_PROD], cons[N_CONS];

    for (int i = 0; i < N_PROD; i++) {
        if (pthread_create(&prod[i], NULL, producer_main, (void *)(long)i) != 0) {
            perror("pthread_create");
            return EXIT_FAILURE;
        }
    }
    for (int i = 0; i < N_CONS; i++) {
        if (pthread_create(&cons[i], NULL, consumer_main, NULL) != 0) {
            perror("pthread_create");
            return EXIT_FAILURE;
        }
    }

    for (int i = 0; i < N_PROD; i++)
        pthread_join(prod[i], NULL);

    /* 生产者全部结束(FIFO 保证真数据都在队列里)→ 投放哨兵广播关闭 */
    for (int i = 0; i < N_CONS; i++)
        buf_put(SENTINEL);

    for (int i = 0; i < N_CONS; i++)
        pthread_join(cons[i], NULL);

    check(bb.count == 0, "buffer drained (all items + sentinels consumed)");
    int each_once = 1;
    for (int i = 0; i < TOTAL_ITEMS; i++) {
        if (seen[i] != 1) {
            each_once = 0;
            printf("  item %d seen %d times\n", i, seen[i]);
        }
    }
    check(each_once, "every item consumed exactly once (no dup/loss)");

    puts("producer-consumer (pthread) passed");
    return 0;
}
