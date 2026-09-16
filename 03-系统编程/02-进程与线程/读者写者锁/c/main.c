/*
 * 读者-写者锁:C 实现(pthread_rwlock)
 *
 * 场景:
 *   A. 并发正确性:读者重叠、写者独占(不变量全程检查)
 *   B. tryrdlock / trywrlock 的 EBUSY 非阻塞探测
 *   C. 递归读锁:同线程 rdlock x3,须 unlock x3(POSIX 允许)
 *
 * 依据 Open Group pthread_rwlock_rdlock 规范(见 README 参考资料)。
 * 注:写者偏好/读者偏好由实现自定义,本 demo 不对其行为做断言。
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

#define N_READERS 4
#define N_WRITERS 3
#define ITER 200

static pthread_rwlock_t rw = PTHREAD_RWLOCK_INITIALIZER;

/* 统计段:独立互斥锁保护,不与读写锁交叉(避免自我阻塞) */
static pthread_mutex_t stats_mu = PTHREAD_MUTEX_INITIALIZER;
static int cur_readers = 0;      /* 当前在读者临界区内的线程数 */
static int max_readers = 0;      /* 观测到的最大并发读者数 */
static int invariant_violations = 0;

static void check(int cond, const char *label)
{
    if (cond) {
        printf("PASS: %s\n", label);
    } else {
        printf("FAIL: %s\n", label);
        exit(EXIT_FAILURE);
    }
}

static void *reader_main(void *arg)
{
    (void)arg;
    for (int i = 0; i < ITER; i++) {
        pthread_rwlock_rdlock(&rw);
        /* 持读锁期间只有读者可以同时进入:统计并发度 */
        pthread_mutex_lock(&stats_mu);
        cur_readers++;
        if (cur_readers > max_readers)
            max_readers = cur_readers;
        pthread_mutex_unlock(&stats_mu);

        /* 微小驻留:给其他读者制造重叠窗口 */
        for (volatile int spin = 0; spin < 2000; spin++)
            ;

        pthread_mutex_lock(&stats_mu);
        cur_readers--;
        pthread_mutex_unlock(&stats_mu);
        pthread_rwlock_unlock(&rw);
    }
    return NULL;
}

static void *writer_main(void *arg)
{
    (void)arg;
    for (int i = 0; i < ITER; i++) {
        pthread_rwlock_wrlock(&rw);
        /* 写者独占段:此时绝不能有读者、也不能有第二个写者 */
        pthread_mutex_lock(&stats_mu);
        if (cur_readers != 0)
            invariant_violations++;
        pthread_mutex_unlock(&stats_mu);

        for (volatile int spin = 0; spin < 1000; spin++)
            ;

        pthread_rwlock_unlock(&rw);
    }
    return NULL;
}

static void demo_trylocks(void)
{
    /* 写者持锁时 tryrdlock 必 EBUSY */
    pthread_rwlock_wrlock(&rw);
    int rc = pthread_rwlock_tryrdlock(&rw);
    check(rc == EBUSY, "tryrdlock while write-held returns EBUSY");
    pthread_rwlock_unlock(&rw);

    /* 读者持锁时 trywrlock 必 EBUSY */
    pthread_rwlock_rdlock(&rw);
    rc = pthread_rwlock_trywrlock(&rw);
    check(rc == EBUSY, "trywrlock while read-held returns EBUSY");
    pthread_rwlock_unlock(&rw);

    /* 空闲时两者都成功 */
    rc = pthread_rwlock_tryrdlock(&rw);
    check(rc == 0, "tryrdlock on free lock succeeds");
    pthread_rwlock_unlock(&rw);
}

static void demo_recursive_rdlock(void)
{
    /* POSIX:同一线程可持多个并发读锁,须配对解锁;
     * glibc 实现下同线程 rdlock x3 后计数为 3,解锁 3 次后可被写者获取 */
    pthread_rwlock_rdlock(&rw);
    pthread_rwlock_rdlock(&rw);
    pthread_rwlock_rdlock(&rw);
    int rc = pthread_rwlock_trywrlock(&rw);
    check(rc == EBUSY, "3 recursive read locks block writers");
    pthread_rwlock_unlock(&rw);
    pthread_rwlock_unlock(&rw);
    rc = pthread_rwlock_trywrlock(&rw);
    check(rc == EBUSY, "1 remaining read lock still blocks writers");
    pthread_rwlock_unlock(&rw);
    rc = pthread_rwlock_trywrlock(&rw);
    check(rc == 0, "all 3 read locks released -> writer can enter");
    pthread_rwlock_unlock(&rw);
}

int main(void)
{
    pthread_t readers[N_READERS], writers[N_WRITERS];

    for (int i = 0; i < N_READERS; i++) {
        if (pthread_create(&readers[i], NULL, reader_main, NULL) != 0) {
            perror("pthread_create");
            return EXIT_FAILURE;
        }
    }
    for (int i = 0; i < N_WRITERS; i++) {
        if (pthread_create(&writers[i], NULL, writer_main, NULL) != 0) {
            perror("pthread_create");
            return EXIT_FAILURE;
        }
    }
    for (int i = 0; i < N_READERS; i++)
        pthread_join(readers[i], NULL);
    for (int i = 0; i < N_WRITERS; i++)
        pthread_join(writers[i], NULL);

    check(invariant_violations == 0, "no readers inside writer sections");
    check(max_readers >= 2, "readers actually overlapped");

    demo_trylocks();
    demo_recursive_rdlock();

    puts("rwlock demo passed");
    return 0;
}
